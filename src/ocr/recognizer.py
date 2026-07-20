"""
武将名称识别模块

使用 PaddleOCR 对 8 个武将名称区域进行 OCR 识别。
识别策略：
  1. 全量字典（ch）PaddleOCR 识别
  2. 用 165 名武将名称库做编辑距离矫正，解决形近字误识别问题
     （不过滤置信度，始终执行矫正——OCR 有时高置信度也出错）

预处理操作在图像层面：放大、自适应对比度增强、锐化。
PaddleOCR 延迟加载，首次调用时初始化。
多维汉字相似度所使用的特征数据存储在 char_info_cache.json 中。
如遇缓存未收录的汉字，会在运行时通过原始库动态补齐。
"""

from __future__ import annotations

import difflib
import json
import logging
import traceback
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# 8 个武将名称的默认 ROI 坐标（基于 2560×1440 分辨率）
_DEFAULT_GENERALS_ROI = [
    [155, 370, 50, 145],
    [440, 370, 50, 145],
    [720, 370, 50, 145],
    [1005, 370, 50, 145],
    [1330, 370, 50, 145],
    [1615, 370, 50, 145],
    [1895, 370, 50, 145],
    [2175, 370, 50, 145],
]

# 两段式识别阈值
_EDIT_DISTANCE_THRESHOLD = 1
_HIGH_CONFIDENCE = 0.995       # 极高置信度——跳过矫正，保护新武将

# ── 汉字特征库 ──────────────────────────────────────────────────────
# 用于多维度视觉相似度决胜（四角号码、仓颉码、部首、拼音、笔画数）
# 优先从 data/char_info_cache.json 加载（启动加速），
# 未收录的汉字在运行时通过原始库（cnradical、pypinyin 等）动态补齐。
_CHAR_INFO_CACHE: dict[str, dict] | None = None
_CHAR_INFO_PATH = Path(__file__).resolve().parent.parent / "data" / "char_info_cache.json"

# 延迟导入的原始库 handler（仅在运行时缺失时按需初始化）
_RADICAL_CLIENT = None
_UNIHAN_CACHE: dict[str, dict] | None = None  # 从 unihan_etl 导出 CSV 懒加载
_STROKES_CACHE: dict[str, int] | None = None  # 从 Unihan_IRGSources.txt 懒加载（基于 unihan_etl 缓存路径）
_STROKES_PATH: str | None = None  # 延迟解析的 IRGSources 路径


def _get_radical_client():
    """按需初始化 cnradical。"""
    global _RADICAL_CLIENT
    if _RADICAL_CLIENT is None:
        try:
            from cnradical import Radical, RunOption
            _RADICAL_CLIENT = Radical(RunOption.Radical)
        except Exception as e:
            logger.warning("cnradical 初始化失败: %s", e)
            _RADICAL_CLIENT = False
    return _RADICAL_CLIENT if _RADICAL_CLIENT is not False else None


def _get_strokes_path() -> str:
    """通过 unihan_etl API 获取 Unihan_IRGSources.txt 路径，避免硬编码。"""
    global _STROKES_PATH
    if _STROKES_PATH is not None:
        return _STROKES_PATH
    try:
        from unihan_etl.core import Options as _Opt
        _path_obj = _Opt().work_dir / "Unihan_IRGSources.txt"
        _STROKES_PATH = str(_path_obj)
    except Exception as e:
        logger.warning("UNIHAN IRGSources 路径获取失败: %s", e)
        _STROKES_PATH = ""
    return _STROKES_PATH


def _load_strokes() -> dict[str, int]:
    """从 UNIHAN IRGSources 懒加载笔画数（kTotalStrokes）。"""
    global _STROKES_CACHE
    if _STROKES_CACHE is not None:
        return _STROKES_CACHE
    irg_path = _get_strokes_path()
    if not irg_path:
        _STROKES_CACHE = {}
        return _STROKES_CACHE
    try:
        _STROKES_CACHE = {}
        with open(irg_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.strip().split("\t")
                if len(parts) >= 3 and parts[1] == "kTotalStrokes":
                    val = parts[2].strip()
                    ch = chr(int(parts[0][2:], 16))
                    # kTotalStrokes 偶尔有范围值 "9-11"，取第一个
                    if "-" in val:
                        val = val.split("-")[0]
                    _STROKES_CACHE[ch] = int(val)
        logger.debug("笔画数已加载: %d 字", len(_STROKES_CACHE))
    except Exception as e:
        logger.warning("UNIHAN IRGSources 笔画数加载失败: %s", e)
        _STROKES_CACHE = {}
    return _STROKES_CACHE


def _get_stroke(char: str) -> int:
    """查询单个汉字的笔画数（缓存优先，懒加载）。"""
    return _load_strokes().get(char, 0)


def _get_pinyin_of(char: str) -> str:
    """用 pypinyin 获取读音。"""
    try:
        from pypinyin import pinyin, Style
        pys = pinyin(char, style=Style.NORMAL)
        return pys[0][0] if pys else ""
    except Exception:
        return ""


def _query_char_from_unihan(char: str) -> dict:
    """运行时从 UNIHAN 缓存/CSV 中查询字符的仓颉码和四角号码。"""
    global _UNIHAN_CACHE
    if _UNIHAN_CACHE is None:
        try:
            import csv, os as _os
            from unihan_etl.core import Packager, Options

            opts = Options(
                format="csv",
                fields=("kCangjie", "kFourCornerCode"),
                download=False, cache=True, log_level="WARNING",
            )
            pkg = Packager(opts)
            pkg.export()

            csv_path = _os.path.expanduser("~/AppData/Local/Tony Narlock/unihan_etl/unihan.csv")
            _UNIHAN_CACHE = {}
            if _os.path.exists(csv_path):
                with open(csv_path, "r", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        ch = row.get("char", "")
                        if ch:
                            cj = (row.get("kCangjie", "") or "").strip()
                            fc = (row.get("kFourCornerCode", "") or "").strip()[:5]
                            _UNIHAN_CACHE[ch] = {"cangjie": cj, "four_corner": fc}
        except Exception as e:
            logger.warning("UNIHAN 查询失败: %s", e)
            _UNIHAN_CACHE = {}
    return _UNIHAN_CACHE.get(char, {})


def _ensure_char_in_cache(char: str) -> dict | None:
    """确保 char 在 _CHAR_INFO_CACHE 中；缺失时动态补齐。返回该字的信息 dict。"""
    global _CHAR_INFO_CACHE
    entry = _CHAR_INFO_CACHE.get(char)
    if entry is not None:
        return entry

    # 动态补齐
    entry = {"radical": "", "cangjie": "", "four_corner": "", "pinyin": "", "total_strokes": ""}

    # 部首
    radical_client = _get_radical_client()
    if radical_client:
        try:
            entry["radical"] = radical_client.trans_ch(char) or ""
        except Exception:
            pass

    # 仓颉码 & 四角号码
    u_info = _query_char_from_unihan(char)
    entry["cangjie"] = u_info.get("cangjie", "")
    entry["four_corner"] = u_info.get("four_corner", "")

    # 拼音
    entry["pinyin"] = _get_pinyin_of(char)

    # 笔画数（从 UNIHAN IRGSources 懒加载，路径基于 unihan_etl API）
    entry["total_strokes"] = str(_get_stroke(char))
    entry["pinyin"] = _get_pinyin_of(char)

    logger.debug("汉字特征动态补齐: %s (U+%04X)", char, ord(char))
    _CHAR_INFO_CACHE[char] = entry
    return entry


def _load_char_info() -> dict[str, dict]:
    """加载汉字特征缓存（优先 JSON 加速，缺失时动态补齐）。"""
    global _CHAR_INFO_CACHE
    if _CHAR_INFO_CACHE is None:
        path = _CHAR_INFO_PATH
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _CHAR_INFO_CACHE = json.load(f)
                logger.debug("汉字特征缓存已加载: %s (%d 字)", path, len(_CHAR_INFO_CACHE))
            except Exception as e:
                logger.warning("汉字特征缓存加载失败: %s", e)
                _CHAR_INFO_CACHE = {}
        else:
            logger.debug("汉字特征缓存不存在: %s", path)
            _CHAR_INFO_CACHE = {}
    return _CHAR_INFO_CACHE


def _hc(char_db: dict, char: str, key: str, default: str = "") -> str:
    """安全地从汉字特征缓存中取值（自动补齐缺失字符）。"""
    entry = char_db.get(char)
    if entry is None:
        # char 完全不在缓存中 → 动态补齐并写回
        _ensure_char_in_cache(char)
        entry = char_db.get(char, {})
    return entry.get(key, default) if entry else default


# ── 评分维度 ─────────────────────────────────────────────────────────

def _four_corner_score(c1: str, c2: str, char_db: dict) -> float:
    """四角号码得分：5位码值中相同的位数比率（0~1）。"""
    fc1 = "".join(c for c in _hc(char_db, c1, "four_corner") if c.isdigit())
    fc2 = "".join(c for c in _hc(char_db, c2, "four_corner") if c.isdigit())
    # 补足/截断到 5 位（原始数据可能不足 5 位或含附加码）
    fc1 = (fc1 + "00000")[:5]
    fc2 = (fc2 + "00000")[:5]
    matches = sum(1 for a, b in zip(fc1, fc2) if a == b)
    return matches / 5.0


def _cangjie_score(c1: str, c2: str, char_db: dict) -> float:
    """仓颉码得分：序列匹配比率（0~1）。"""
    cj1 = _hc(char_db, c1, "cangjie")
    cj2 = _hc(char_db, c2, "cangjie")
    if not cj1 or not cj2:
        return 0.0
    return difflib.SequenceMatcher(None, cj1, cj2).ratio()


def _radical_score(c1: str, c2: str, char_db: dict) -> float:
    """部首得分：相同为 1，否则为 0。"""
    r1 = _hc(char_db, c1, "radical")
    r2 = _hc(char_db, c2, "radical")
    return 1.0 if r1 and r2 and r1 == r2 else 0.0


# 多维评分权重：四角号码 40% + 仓颉码 40% + 部首 20%
_FC_WEIGHT = 0.4
_CJ_WEIGHT = 0.4
_RD_WEIGHT = 0.2


def _multi_dim_similarity(c1: str, c2: str, char_db: dict) -> float:
    """多维汉字相似度评分（加权），范围 [0, 1]。"""
    return _four_corner_score(c1, c2, char_db) * _FC_WEIGHT \
           + _cangjie_score(c1, c2, char_db) * _CJ_WEIGHT \
           + _radical_score(c1, c2, char_db) * _RD_WEIGHT


# ── 平局处理维度 ───────────────────────────────────────────────────

def _pinyin_similarity(c1: str, c2: str, char_db: dict) -> float:
    """拼音读音相似度：相同为 1，完全不同为 0。"""
    py1 = _hc(char_db, c1, "pinyin")
    py2 = _hc(char_db, c2, "pinyin")
    if not py1 or not py2:
        return 0.0
    return 1.0 if py1 == py2 else 0.0


def _stroke_diff(c1: str, c2: str, char_db: dict) -> int:
    """笔画数差绝对值。"""
    s1_str = _hc(char_db, c1, "total_strokes")
    s2_str = _hc(char_db, c2, "total_strokes")
    s1 = int(s1_str) if s1_str else _get_stroke(c1)
    s2 = int(s2_str) if s2_str else _get_stroke(c2)
    return abs(s1 - s2)


def _levenshtein_distance(s1: str, s2: str) -> int:
    """计算两个字符串的编辑距离。"""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            cost = 0 if c1 == c2 else 1
            curr_row.append(min(
                curr_row[j] + 1,
                prev_row[j + 1] + 1,
                prev_row[j] + cost,
            ))
        prev_row = curr_row
    return prev_row[-1]


def _pick_visually_similar(text: str, candidates: list[str]) -> str:
    """从编辑距离相同的候选中选出最相似的一个。

    评分规则（逐字符比较）：
      1. 主评分：四角(40%) + 仓颉码(40%) + 部首(20%)，每相同字符 +1.0
      2. 长度惩罚：每多/少一个字 -0.5，多余字符再扣 -0.5/个
      3. 平局时追加「拼音相似度」「笔画数差」排序
    """
    char_db = _load_char_info()
    scored: list[tuple[float, str]] = []

    for candidate in candidates:
        score = 0.0
        # 字符级逐位比较
        for tc, cc in zip(text, candidate):
            if tc == cc:
                score += 1.0  # 加权后满分 1.0
            else:
                score += _multi_dim_similarity(tc, cc, char_db)

        # 长度惩罚
        extra = abs(len(candidate) - len(text))
        score -= 0.5 * extra + 0.5 * extra

        scored.append((score, candidate))

    # 按分数降序排列
    scored.sort(key=lambda x: (-x[0], x[1]))

    # 检查是否存在平局（得分相同的一组）
    i = 0
    while i < len(scored):
        j = i
        while j + 1 < len(scored) and abs(scored[j][0] - scored[j + 1][0]) < 1e-9:
            j += 1
        # 平局组：j > i
        if j > i:
            tie_group = scored[i:j + 1]
            # 平局决胜：拼音相似度降序 → 笔画数差升序
            def tie_key(item: tuple[float, str]) -> tuple[float, int]:
                _, cand = item
                # 拼音相似度：逐字符累加
                py_score = 0.0
                for tc, cc in zip(text, cand):
                    if tc == cc:
                        py_score += 1.0
                    else:
                        py_score += _pinyin_similarity(tc, cc, char_db)
                # 笔画数差：逐字符累加
                stroke_diff_total = 0
                for tc, cc in zip(text, cand):
                    if tc != cc:
                        stroke_diff_total += _stroke_diff(tc, cc, char_db)
                return (-py_score, stroke_diff_total)
            tie_group.sort(key=tie_key)
            scored[i:j + 1] = tie_group
        i = j + 1

    best_match = scored[0][1]
    if best_match != text:
        logger.debug("多维相似度: %s → %s (scores=%s)", text, best_match,
                     [f"{c}={s:.2f}" for s, c in scored])
    return best_match


def _correct_with_hero_list(text: str, hero_names: list[str]) -> str:
    """用武将名称库矫正识别结果。

    Args:
        text: OCR 识别出的文本。
        hero_names: 165 名武将名称列表。

    Returns: 矫正后的武将名（若无匹配或无需矫正则返回原文本）。
    """
    if not text:
        return text

    text = text.strip()

    # 收集编辑距离 ≤ 阈值 的所有候选
    candidates: list[str] = []
    for hero in hero_names:
        dist = _levenshtein_distance(text, hero)
        if dist <= _EDIT_DISTANCE_THRESHOLD:
            candidates.append(hero)

    if not candidates:
        return text

    # 唯一候选 → 直接采纳
    if len(candidates) == 1:
        if candidates[0] != text:
            logger.debug("矫正: %s → %s", text, candidates[0])
        return candidates[0]

    # 多个候选 → 多维相似度决胜（四角号码+仓颉码+部首+拼音+笔画）
    best_match = _pick_visually_similar(text, candidates)
    if best_match != text:
        logger.debug("矫正: %s → %s (候选=%s)", text, best_match, candidates)
    return best_match


class GeneralRecognizer:
    """武将名称识别器，支持全量字典 + 武将名库矫正。"""

    def __init__(self, rois: list[list[int]] | None = None,
                 hero_names: list[str] | None = None,
                 reference_size: tuple[int, int] = (2560, 1440)) -> None:
        self._rois = rois or _DEFAULT_GENERALS_ROI
        self._hero_names = hero_names or []
        self._reference_size = reference_size
        self._ocr = None  # PaddleOCR 引擎（延迟加载）

    # ── OCR 引擎 ──────────────────────────────────────────────────────

    @property
    def _engine(self):
        """PaddleOCR（ch），延迟加载。"""
        if self._ocr is None:
            logger.info("首次调用，正在加载 PaddleOCR 模型...")
            try:
                from paddleocr import PaddleOCR
                self._ocr = PaddleOCR(use_angle_cls=False, lang="ch", show_log=False)
                logger.info("PaddleOCR 模型加载完成")
            except Exception as e:
                logger.error("PaddleOCR 模型加载失败: %s", e)
                logger.debug(traceback.format_exc())
                raise
        return self._ocr

    # ── 提前初始化 ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """提前加载 OCR 模型及汉字特征缓存，避免首次识别时的延迟。"""
        _ = self._engine
        # 预热汉字特征缓存（加载 JSON + 预加载 pypinyin，让动态补齐的首次开销不在识别时发生）
        _load_char_info()
        try:
            from pypinyin import pinyin, Style
            # 预加载一次，让后续查询零开销
            _ = pinyin("一", style=Style.NORMAL)
        except Exception:
            pass

    # ── 识别 ──────────────────────────────────────────────────────────

    def recognize(self, image: np.ndarray | Image.Image) -> list[dict]:
        """对 8 个武将区域逐一识别，返回含置信度的结果。

        Args:
            image: 截图图像。

        Returns:
            [{index: int, name: str, confidence: float}, ...]
        """
        if isinstance(image, Image.Image):
            image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

        image_height, image_width = image.shape[:2]
        reference_width, reference_height = self._reference_size
        scale_x = image_width / reference_width
        scale_y = image_height / reference_height
        logger.debug("武将 ROI 缩放: %.4f×%.4f，当前截图=%sx%s，参考=%sx%s",
                     scale_x, scale_y, image_width, image_height,
                     reference_width, reference_height)

        results: list[dict] = []
        for i, (x, y, w, h) in enumerate(self._rois):
            roi_x = round(x * scale_x)
            roi_y = round(y * scale_y)
            roi_w = max(1, round(w * scale_x))
            roi_h = max(1, round(h * scale_y))
            logger.info(
                "武将 %d OCR ROI: x=%d, y=%d, w=%d, h=%d (参考 ROI=%s)",
                i + 1, roi_x, roi_y, roi_w, roi_h, [x, y, w, h],
            )
            roi_img = image[roi_y:roi_y + roi_h, roi_x:roi_x + roi_w]
            name, confidence = self._recognize_single(roi_img, i + 1)
            results.append({"index": i + 1, "name": name, "confidence": round(confidence, 4)})
            logger.debug("武将 %d 识别: %s (置信度=%.4f)", i + 1, name or "(空)", confidence)

        return results

    def _recognize_single(self, roi: np.ndarray, slot: int) -> tuple[str, float]:
        """识别单个武将名称区域。"""
        try:
            prepared = self._preprocess_roi(roi)
            result = self._engine.ocr(prepared, cls=False)
            text, conf = self._extract_text(result)
            logger.info(
                "武将 %d OCR 原始结果: text=%r, confidence=%.4f",
                slot, text, conf,
            )

            if not text:
                return "", 0.0

            # 极高置信度 + OCR 文本不在武将库 → 信任 OCR（保护新武将）
            if self._hero_names and conf >= _HIGH_CONFIDENCE and text not in self._hero_names:
                logger.debug("武将 %d: 高置信度新名 '%s'，跳过矫正", slot, text)
                return text, conf

            # 第二段矫正：用武将名库验证 OCR 结果
            if self._hero_names:
                corrected = _correct_with_hero_list(text, self._hero_names)
                if corrected != text:
                    logger.debug("武将 %d: 矫正 %s → %s", slot, text, corrected)
                    return corrected, conf

            return text, conf

        except Exception as e:
            logger.warning("武将 %d 识别异常: %s", slot, e)
            logger.debug(traceback.format_exc())

        return "", 0.0

    # ── 图像预处理 ────────────────────────────────────────────────────

    @staticmethod
    def _preprocess_roi(roi: np.ndarray) -> np.ndarray:
        """预处理 ROI 区域：放大 3× → CLAHE → 锐化 → 灰度。"""
        enlarged = cv2.resize(roi, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)

        lab = cv2.cvtColor(enlarged, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        l = clahe.apply(l)
        enhanced = cv2.merge([l, a, b])
        enhanced = cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)

        kernel = np.array([[-1, -1, -1],
                           [-1,  9, -1],
                           [-1, -1, -1]], dtype=np.float32)
        sharpened = cv2.filter2D(enhanced, -1, kernel)

        return cv2.cvtColor(sharpened, cv2.COLOR_BGR2GRAY)

    # ── 辅助 ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract_text(ocr_result: list | None) -> tuple[str, float]:
        """从 PaddleOCR 返回结果中提取文字和置信度。"""
        if not ocr_result or not ocr_result[0]:
            return "", 0.0
        for line in ocr_result[0]:
            text = line[1][0].strip()
            confidence = line[1][1]
            if text:
                return text, confidence
        return "", 0.0

    # ── 保存结果 ──────────────────────────────────────────────────────

    @staticmethod
    def save_results(results: list[dict], json_path: str | Path, image_path: str | Path | None = None) -> None:
        """将识别结果保存为 JSON 文件。"""
        data = {
            "image": str(image_path) if image_path else "",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "page_type": "wujiang_select",
            "generals": results,
        }
        json_path = Path(json_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info("识别结果已保存: %s", json_path)
        except Exception as e:
            logger.error("识别结果保存失败 %s: %s", json_path, e)
