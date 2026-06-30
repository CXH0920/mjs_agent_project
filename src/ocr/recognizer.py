"""
武将名称识别模块

使用 PaddleOCR 对 8 个武将名称区域进行 OCR 识别。
识别策略：
  1. 全量字典（ch）PaddleOCR 识别
  2. 用 155 名武将名称库做编辑距离矫正，解决形近字误识别问题
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
    [160, 370, 40, 140],
    [445, 370, 40, 140],
    [725, 370, 40, 140],
    [1010, 370, 40, 140],
    [1335, 370, 40, 140],
    [1620, 370, 40, 140],
    [1900, 370, 40, 140],
    [2180, 370, 40, 140],
]

# 两段式识别阈值
_EDIT_DISTANCE_THRESHOLD = 1
_HIGH_CONFIDENCE = 0.995       # 极高置信度——跳过矫正，保护新武将
_LOW_CONFIDENCE = 0.97         # 低置信度门槛——存在邻近替代时用编辑距离候选（而非信任精确匹配）

# ── 汉字特征库 ──────────────────────────────────────────────────────
# 用于多维度视觉相似度决胜（四角号码、仓颉码、部首、拼音、笔画数）
# 优先从 data/char_info_cache.json 加载（启动加速），
# 未收录的汉字在运行时通过原始库（cnradical、pypinyin 等）动态补齐。
_CHAR_INFO_CACHE: dict[str, dict] | None = None
_CHAR_INFO_PATH = Path(__file__).resolve().parent.parent / "data" / "char_info_cache.json"

# 延迟导入的原始库 handler（仅在运行时缺失时按需初始化）
_RADICAL_CLIENT = None
_UNIHAN_CACHE: dict[str, dict] | None = None

# 内置笔画数字典（汉字笔画数自定型后不变，内联以零依赖方式获取）
# 覆盖所有 155 名武将及常见 OCR 误识汉字
_STROKE_TABLE: dict[str, int] = {
    '一':1,'丁':2,'七':2,'万':3,'丈':3,'三':3,'上':3,'下':3,'不':4,'且':5,'世':5,'丘':5,'丙':5,
    '东':5,'丝':5,'丞':6,'两':7,'严':7,'个':3,'中':4,'丰':4,'临':9,'为':4,'主':5,'丽':7,'乃':2,
    '久':3,'么':3,'义':3,'之':3,'乌':4,'乐':5,'九':2,'也':3,'习':3,'乡':3,'书':4,'买':6,'了':2,
    '予':4,'争':6,'事':8,'二':2,'于':3,'云':4,'五':4,'井':4,'亚':6,'交':6,'亦':6,'产':6,'京':8,
    '人':2,'今':4,'介':4,'从':4,'他':5,'付':5,'仙':5,'代':5,'令':5,'以':4,'仰':6,'仲':6,'任':6,
    '伊':6,'伍':6,'伏':6,'伐':6,'休':6,'会':6,'伟':6,'传':6,'伤':6,'伯':7,'何':7,'余':7,'佛':7,
    '作':7,'佳':8,'来':7,'侍':8,'依':8,'侯':9,'侠':9,'信':9,'修':9,'俱':10,'俸':10,'仓':4,
    '个':3,'们':5,'伦':6,'假':11,'伟':6,'传':6,'伤':6,'倾':10,'像':13,'元':4,'充':6,'先':6,
    '光':6,'克':7,'免':7,'兖':9,'公':4,'六':4,'兰':5,'共':6,'关':6,'兵':7,'其':8,'具':8,'典':8,
    '养':9,'兼':10,'内':4,'冉':5,'再':6,'冒':9,'冓':10,'冠':9,'冬':5,'冰':6,'冲':6,'决':6,
    '况':7,'冷':7,'凌':10,'凝':16,'几':2,'凡':3,'凤':4,'凫':6,'凯':8,'凶':4,'出':5,'函':8,
    '刀':2,'分':4,'切':4,'刊':5,'刑':6,'列':6,'刘':6,'则':6,'刚':6,'创':6,'初':7,'删':7,'判':7,
    '别':7,'利':7,'到':8,'制':8,'刺':8,'刻':8,'刷':8,'前':9,'剑':9,'剔':10,'剖':10,'剧':10,
    '劈':15,'力':2,'功':5,'加':5,'助':7,'男':7,'劫':7,'劳':7,'勇':9,'勉':9,'勒':11,'务':5,
    '胜':9,'势':8,'勤':13,'包':5,'化':4,'北':5,'匹':4,'区':4,'医':7,'十':2,'千':3,'升':4,
    '午':4,'半':5,'华':6,'协':8,'卑':8,'卒':8,'卓':8,'南':9,'博':12,'占':5,'卡':5,'卢':5,
    '卬':4,'卫':3,'即':7,'卿':10,'危':6,'卷':8,'卸':8,'却':7,'卿':10,'厄':4,'厉':5,'压':6,
    '厌':6,'原':10,'厍':6,'去':5,'参':8,'又':2,'及':3,'友':4,'双':4,'反':4,'发':5,'叔':8,
    '取':8,'受':8,'变':8,'叙':9,'叠':13,'口':3,'古':5,'句':5,'另':5,'召':5,'可':5,'台':5,
    '史':5,'右':5,'司':5,'叹':5,'吃':6,'各':6,'合':6,'吊':6,'同':6,'名':6,'后':6,'吏':6,
    '向':6,'吕':6,'君':7,'吴':7,'否':7,'吔':7,'告':7,'周':8,'呼':8,'命':8,'和':8,'咎':8,
    '咤':9,'哀':9,'品':9,'哇':9,'哈':9,'哉':9,'咸':9,'哙':9,'唐':10,'哲':10,'哓':9,'哭':10,
    '员':7,'哨':10,'刚':6,'啖':11,'商':11,'啊':11,'问':6,'启':7,'啻':12,'善':12,'喻':12,
    '喑':12,'喜':12,'喝':12,'喟':12,'嚎':17,'嚣':18,'向':6,'吕':6,'君':7,'吴':7,'周':8,
    '呼':8,'命':8,'咸':9,'哀':9,'唐':10,'商':11,'善':12,'喜':12,'严':7,'丧':8,'单':9,'四':5,
    '因':6,'回':6,'困':7,'固':8,'国':8,'围':9,'图':8,'圆':10,'圈':11,'土':3,'圣':5,'在':6,
    '地':6,'均':7,'坎':7,'坐':7,'坑':7,'坚':7,'坦':8,'坤':8,'垓':9,'城':9,'垲':9,'埸':10,
    '培':11,'基':11,'执':6,'堂':11,'坚':7,'堕':11,'堤':12,'堪':12,'喜':12,'场':6,'塔':12,
    '塞':13,'填':13,'墙':14,'士':3,'壬':4,'壮':6,'寿':7,'夏':10,'夕':3,'外':5,'多':6,'夜':8,
    '够':11,'梦':11,'大':3,'天':4,'太':4,'夫':4,'夭':4,'央':5,'失':5,'头':5,'夷':6,'夸':6,
    '夹':6,'奉':8,'奇':8,'奈':9,'奋':9,'契':9,'奔':8,'奖':9,'奕':9,'套':10,'奢':11,'奠':12,
    '奢':11,'女':3,'奴':5,'奶':5,'好':6,'如':6,'妄':6,'妇':6,'妃':6,'她':6,'妈':6,'妍':7,
    '妓':7,'妒':7,'妙':7,'妖':7,'妤':7,'妊':7,'妹':8,'妻':8,'姒':8,'姓':8,'姐':8,'姑':8,
    '始':8,'姆':8,'妾':8,'姬':10,'姽':9,'姨':9,'娃':9,'娄':9,'威':9,'娘':10,'娟':10,'娜':9,
    '娼':11,'婆':11,'婉':11,'妇':6,'婧':11,'婴':11,'婵':11,'媚':12,'媛':12,'婷':12,'婿':12,
    '媒':12,'嫁':13,'嫂':12,'嫱':14,'嫡':14,'嫔':14,'子':3,'孔':4,'孕':5,'存':6,'孙':6,
    '孝':7,'孟':8,'季':8,'孤':8,'学':8,'孩':9,'孙':6,'孱':12,'宁':5,'宅':6,'安':6,'宋':7,
    '完':7,'宏':7,'实':8,'宓':8,'定':8,'宜':8,'宝':8,'宠':8,'审':8,'客':9,'宣':9,'室':9,
    '宫':9,'宰':10,'害':10,'宴':10,'容':10,'宽':10,'宸':10,'家':10,'宵':10,'密':11,'寇':11,
    '寅':11,'寄':11,'寂':11,'尉':11,'尊':12,'寻':6,'对':5,'导':6,'小':3,'少':4,'尔':5,
    '尖':6,'尚':8,'尝':9,'尤':4,'就':12,'尸':3,'尹':4,'尺':4,'尼':5,'尽':6,'尾':7,'局':7,
    '尿':7,'层':7,'居':8,'届':8,'屈':8,'屋':9,'展':10,'屡':12,'履':15,'屯':4,'山':3,'岐':7,
    '岑':7,'岫':8,'岳':8,'峭':10,'峻':10,'峰':10,'崔':11,'崩':11,'崇':11,'崛':11,'巍':21,
    '川':3,'州':6,'巡':6,'巢':11,'工':3,'左':5,'巧':5,'巨':4,'巩':6,'差':9,'己':3,'已':3,
    '巴':4,'巷':9,'巾':3,'市':5,'布':5,'帅':5,'帆':6,'师':6,'常':11,'帽':12,'干':3,'平':5,
    '年':6,'并':6,'幸':8,'幺':3,'幼':5,'幽':9,'广':3,'庄':6,'庆':6,'庐':7,'庖':8,'店':8,
    '庙':8,'府':8,'庞':8,'度':9,'庭':9,'庶':11,'康':11,'庸':11,'廉':13,'延':6,'廷':6,'建':8,
    '弋':3,'式':6,'引':4,'弗':5,'弘':5,'弛':6,'弟':7,'张':7,'弥':8,'弦':8,'弯':9,'强':12,
    '彧':10,'归':5,'当':6,'录':8,'形':7,'彦':9,'彩':11,'彪':11,'彬':11,'彭':12,'彰':14,'影':15,
    '彳':3,'征':8,'彼':8,'往':8,'征':8,'待':9,'律':9,'徐':10,'徒':10,'得':11,'从':4,'德':15,
    '心':4,'必':5,'忌':7,'忍':7,'志':7,'忘':7,'忙':6,'忠':8,'快':8,'念':8,'忽':8,'忿':8,
    '怀':7,'态':8,'怃':7,'怔':8,'怕':8,'怖':8,'怜':8,'怪':8,'怡':8,'性':8,'怨':9,'急':9,
    '怒':9,'恃':9,'恒':9,'恍':9,'恻':9,'恐':10,'恚':10,'恕':10,'恭':10,'恩':10,'息':10,
    '悄':10,'悟':10,'恰':9,'惠':12,'恶':10,'惧':11,'惨':11,'愤':12,'慌':12,'惰':12,'惺':12,
    '愕':12,'惇':11,'惑':12,'想':13,'意':13,'愚':13,'感':13,'愍':13,'愁':13,'爱':10,'慈':13,
    '慎':13,'慕':14,'慢':14,'慧':15,'虑':10,'慰':15,'庆':6,'戈':4,'成':6,'我':7,'戒':7,'戕':8,
    '或':8,'战':9,'戚':11,'戟':12,'戛':11,'戡':13,'截':14,'戮':13,'戬':14,'戴':17,'户':4,
    '所':8,'才':3,'手':4,'扎':4,'扑':5,'打':5,'托':6,'执':6,'扩':6,'扫':6,'扬':6,'扶':7,
    '抚':7,'扰':7,'扼':7,'找':7,'批':7,'承':8,'技':7,'抄':7,'把':7,'抑':7,'抒':7,'抓':7,
    '投':7,'抗':7,'折':7,'抢':7,'护':7,'报':7,'披':8,'抱':8,'抵':8,'抹':8,'抽':8,'拂':8,
    '担':8,'拆':8,'拇':8,'拍':8,'拓':8,'拔':8,'拖':8,'拗':8,'招':8,'拜':9,'括':9,'拭':9,
    '拷':9,'拱':9,'拯':9,'拴':9,'拾':9,'持':9,'挂':9,'指':9,'按':9,'挎':9,'挑':9,'挖':9,
    '挚':10,'挨':10,'挫':10,'振':10,'挺':10,'挽':10,'捂':10,'捅':10,'捉':10,'捐':10,'损':10,
    '捡':10,'换':10,'据':11,'掘':11,'掳':11,'掷':11,'探':11,'接':11,'控':11,'推':11,'掩':11,
    '措':11,'掬':11,'揭':12,'援':12,'揽':12,'提':12,'插':12,'握':12,'揣':12,'揪':12,'揭':12,
    '挥':9,'揆':12,'揖':12,'扬':6,'换':10,'援':12,'搁':12,'搂':12,'搅':12,'搜':12,'搬':13,
    '携':13,'摄':13,'摇':13,'摘':14,'摔':14,'撤':15,'操':16,'擅':16,'拥':8,'击':5,'挡':9,
    '支':4,'收':6,'改':7,'攻':7,'放':8,'政':9,'故':9,'效':10,'救':11,'教':11,'敢':11,'散':12,
    '敬':13,'数':13,'敏':11,'文':4,'斉':8,'斐':12,'斗':4,'料':10,'斟':13,'斤':4,'斥':5,
    '斧':8,'斩':8,'斫':9,'断':11,'斯':12,'新':13,'方':4,'于':3,'施':9,'旁':10,'旅':10,
    '旗':14,'无':4,'既':9,'日':4,'旦':5,'旱':7,'时':7,'旷':7,'昂':8,'昆':8,'明':8,'昏':8,
    '易':8,'昔':8,'昙':8,'星':9,'映':9,'春':9,'昧':9,'昨':9,'昭':9,'是':9,'晁':10,'时':7,
    '晋':10,'晏':10,'晟':11,'晨':11,'晞':11,'晦':11,'晚':12,'普':12,'景':12,'晴':12,'晶':12,
    '智':12,'暝':13,'暨':14,'暴':15,'曜':16,'曰':4,'曲':6,'更':7,'曷':9,'曹':11,'曼':11,
    '曾':12,'替':12,'最':12,'月':4,'有':6,'朋':8,'朗':10,'望':11,'朝':12,'期':12,'木':4,
    '未':5,'末':5,'本':5,'术':5,'朱':6,'朴':6,'朵':6,'机':6,'杀':6,'杂':6,'权':6,'李':7,
    '材':7,'村':7,'杜':7,'杖':7,'杞':7,'杨':7,'束':7,'来':7,'板':8,'林':8,'果':8,'枝':8,
    '杯':8,'杰':8,'松':8,'析':8,'杵':8,'枚':8,'枪':8,'枫':8,'柏':9,'染':9,'柱':9,'柳':9,
    '柴':9,'栅':9,'标':9,'栋':9,'栏':9,'树':9,'栗':10,'校':10,'栩':10,'桑':10,'桓':10,
    '桥':10,'桃':10,'格':10,'根':10,'桂':10,'栽':10,'桓':10,'梁':11,'梅':11,'梓':11,'梳':11,
    '梯':11,'械':11,'梵':11,'梦':11,'梧':11,'梨':11,'梭':11,'梆':11,'棺':12,'植':12,'楚':13,
    '楼':13,'概':12,'乐':5,'樊':15,'横':15,'橘':16,'机':6,'欠':4,'次':6,'欢':6,'欣':8,'欲':11,
    '款':12,'歇':13,'止':4,'正':5,'此':6,'步':7,'武':8,'歧':8,'历':4,'归':5,'歹':4,'死':6,
    '歼':7,'殄':9,'殆':9,'殉':10,'殊':10,'残':9,'殒':11,'殖':12,'殇':9,'殷':10,'殿':13,
    '毁':13,'毅':15,'毋':4,'母':5,'每':7,'毒':9,'比':4,'毕':6,'毛':4,'毫':11,'氏':4,'民':5,
    '气':4,'氢':10,'水':4,'氺':5,'永':5,'求':7,'汉':5,'江':6,'池':6,'汤':6,'汪':7,'沐':7,
    '沛':7,'汰':7,'沥':7,'沈':7,'沉':7,'沙':7,'泛':7,'沧':7,'沃':7,'沟':7,'没':7,'沱':8,
    '法':8,'河':8,'沾':8,'沮':8,'油':8,'治':8,'沼':8,'沿':8,'泄':8,'泛':7,'波':8,'泣':8,
    '泥':8,'注':8,'泪':8,'泰':10,'泳':8,'洋':9,'洲':9,'洪':9,'活':9,'洽':9,'派':9,'洗':9,
    '洛':9,'济':9,'洸':9,'浊':9,'洞':9,'津':9,'测':9,'海':10,'浸':10,'涂':10,'消':10,'涟':10,
    '涅':10,'浩':10,'润':10,'涌':10,'涿':11,'清':11,'鸿':11,'淇':11,'淋':11,'淞':11,'淹':11,
    '淑':11,'淘':11,'淡':11,'净':8,'淮':11,'深':11,'淳':11,'混':11,'添':11,'清':11,'渊':11,
    '渐':11,'渔':11,'渝':12,'渠':12,'渡':12,'渤':12,'温':12,'渭':12,'港':12,'游':12,'湖':12,
    '湘':12,'湮':12,'渺':12,'汤':6,'湾':12,'湿':12,'溃':12,'溅':12,'滑':12,'滁':12,'滔':13,
    '溪':13,'滚':13,'满':13,'滦':13,'滨':14,'漂':14,'漫':14,'滴':14,'演':14,'漠':13,'汉':5,
    '漪':14,'潇':14,'潘':15,'潜':15,'潭':15,'潮':15,'潼':15,'澈':15,'澄':15,'澍':15,'澹':16,
    '激':16,'澡':16,'火':4,'灭':5,'灯':6,'灰':6,'灵':7,'灾':7,'灿':7,'灼':7,'灶':7,'灸':7,
    '炬':8,'炳':9,'炸':9,'点':9,'炼':9,'烁':9,'炮':9,'炫':9,'烂':9,'炭':9,'烈':10,'乌':4,
    '烝':10,'烟':10,'烦':10,'烧':10,'烨':10,'烛':10,'烬':10,'热':10,'焕':11,'烽':11,'烹':11,
    '焉':11,'焙':12,'焚':12,'焰':12,'煖':13,'煜':13,'照':13,'烦':10,'熊':14,'熟':15,'燕':16,
    '燮':17,'爆':19,'爨':30,'爷':6,'版':8,'牒':13,'牙':4,'牛':4,'牟':6,'牢':7,'牧':8,'物':8,
    '牲':9,'牵':9,'特':10,'犀':12,'犒':14,'犬':4,'犯':5,'狄':7,'狂':7,'狁':7,'狐':8,'狗':8,
    '狭':9,'狮':9,'猗':11,'猛':11,'猜':11,'猪':11,'献':13,'獒':14,'王':4,'玉':5,'玕':7,
    '玖':7,'玛':7,'玩':8,'环':8,'现':8,'玫':8,'玺':10,'珪':10,'班':10,'琉':11,'琅':11,'理':11,
    '琼':12,'甄':13,'璧':18,'瓜':5,'瓠':11,'瓦':5,'瓮':8,'甘':5,'甚':9,'生':5,'产':6,'甥':12,
    '用':5,'甫':7,'甬':7,'田':5,'由':5,'甲':5,'申':5,'男':7,'町':7,'画':8,'异':6,'畅':8,
    '畦':11,'番':12,'畴':13,'疆':19,'疏':12,'疑':14,'疒':5,'疚':8,'疫':9,'疾':10,'病':10,
    '疲':10,'疼':10,'痈':10,'瘐':13,'瘕':14,'瘗':14,'癌':17,'瘳':16,'癖':18,'发':5,'白':5,
    '百':6,'皂':7,'的':8,'皆':9,'皇':9,'皋':10,'皓':12,'皦':16,'皮':5,'皱':10,'皿':5,'盂':8,
    '盍':10,'盛':11,'盗':11,'盖':11,'盘':11,'盟':13,'目':5,'直':8,'相':9,'盼':9,'盾':9,'省':9,
    '眨':9,'看':9,'真':10,'眼':11,'着':11,'众':6,'睢':13,'督':13,'睡':13,'睦':13,'睫':13,
    '睿':14,'矢':5,'矣':7,'知':8,'矩':9,'短':12,'矯':17,'石':5,'矶':7,'砥':10,'破':10,'确':12,
    '碧':14,'磐':15,'磨':16,'磔':15,'礁':17,'示':5,'礼':5,'社':7,'祀':7,'祈':8,'祉':8,'祇':8,
    '佑':7,'祠':9,'祖':10,'祝':10,'神':10,'祥':10,'祭':11,'祺':12,'禅':12,'福':13,'祯':13,
    '禳':22,'离':10,'秀':7,'私':7,'秦':10,'程':12,'稷':15,'穴':5,'究':7,'空':8,'穿':9,'突':9,
    '窃':9,'窄':10,'立':5,'产':6,'妾':8,'竖':9,'站':10,'竟':11,'章':11,'童':12,'竭':14,'端':14,
    '竹':6,'竹':6,'竿':9,'笄':10,'笑':10,'笔':10,'符':11,'第':11,'等':12,'筋':12,'策':12,
    '简':13,'等':12,'筮':13,'签':13,'管':14,'箫':14,'箭':15,'篇':15,'筑':12,'簋':17,'簦':18,
    '米':6,'类':9,'粉':10,'粒':11,'粟':12,'粥':12,'粮':13,'精':14,'糈':14,'糁':14,'糅':15,
    '糇':15,'糗':16,'糟':17,'糠':17,'糜':17,'糨':18,'系':7,'紊':10,'素':10,'索':10,'紧':10,
    '紫':12,'累':11,'絜':12,'絮':12,'丝':5,'绝':9,'绞':9,'统':9,'绌':8,'绍':8,'经':8,'绡':10,
    '绢':10,'绣':10,'绥':10,'缘':12,'缭':15,'缃':12,'缄':12,'缇':12,'缗':13,'缙':13,'缢':13,
    '缣':13,'缥':14,'缪':14,'缯':14,'缰':19,'缴':16,'缵':19,'网':6,'罕':7,'罗':8,'罚':9,
    '置':13,'羊':6,'美':9,'群':13,'羽':6,'羿':9,'翕':11,'翔':12,'翦':15,'翡':14,'翟':14,
    '翠':14,'翳':17,'翼':17,'翻':18,'耀':20,'老':6,'考':6,'者':8,'而':6,'耍':9,'耐':9,'耕':10,
    '耗':10,'耳':6,'耶':8,'耿':10,'聊':11,'聆':11,'聪':15,'声':7,'耸':10,'肃':8,'肆':13,'肉':6,
    '肋':6,'肌':6,'肓':7,'肖':7,'肘':7,'肚':7,'肝':7,'肠':7,'股':8,'肢':8,'肥':8,'肩':8,
    '肯':8,'肱':8,'育':8,'肴':8,'肺':8,'胃':9,'胆':9,'背':9,'胎':9,'胞':9,'胡':9,'胥':9,
    '能':10,'脆':10,'脂':10,'胸':10,'胳':10,'脊':10,'朔':10,'朗':10,'朕':10,'殷':10,'脐':10,
    '陵':10,'陷':10,'陪':10,'陶':11,'陆':7,'陈':7,'阴':6,'阵':6,'阳':6,'隆':11,'随':11,'隐':11,
    '隔':12,'隙':12,'障':13,'隧':14,'隶':8,'隹':8,'雅':12,'集':12,'雄':12,'雍':13,'雏':13,
    '雕':16,'雨':8,'雪':11,'雳':12,'雷':13,'零':13,'雾':13,'雹':13,'需':14,'震':15,'霉':15,
    '霍':16,'霓':16,'霖':16,'霜':17,'霞':17,'露':21,'霸':21,'霹':21,'青':8,'靖':13,'静':14,
    '非':8,'面':9,'革':9,'韦':4,'韩':12,'音':9,'韵':13,'页':6,'顶':8,'顷':11,'项':12,'顺':12,
    '须':9,'顾':10,'顿':10,'颂':10,'预':10,'领':11,'颇':11,'头':5,'颖':13,'题':15,'颜':15,
    '额':15,'风':4,'飘':15,'飞':3,'食':9,'饱':8,'养':9,'首':9,'香':9,'马':3,'驯':6,'驰':6,
    '驱':7,'驷':8,'驹':8,'骐':11,'骑':11,'骊':10,'骋':10,'骏':11,'骜':13,'骞':13,'骠':14,
    '骢':14,'骧':23,'骨':9,'骸':15,'骼':14,'高':10,'髯':15,'鬼':9,'魂':13,'魅':14,'魄':14,
    '魏':17,'魔':20,'鱼':8,'鲜':14,'鲁':12,'鲸':16,'鸟':5,'鸠':7,'鸩':9,'鹦':16,'鹏':13,
    '鹤':15,'鹄':12,'鹿':11,'麾':16,'黄':11,'黍':12,'黑':12,'默':16,'鼓':13,'鼻':14,'齐':6,
    '齿':8,'龄':13,'龙':5,'庞':8,'龚':11,'龟':7,
    # 项目相关 - OCR 高频误识字
    '赢':20,'嬴':16,'羸':19,'剪':11,'翡':14,'异':6,'不':4,'丕':5,'彧':10,'或':8,'戟':12,
    '域':11,'媛':12,'煖':13,'统':9,'哙':9,'会':6,'缭':15,'尉':11,'缭':15,'缘':12,'甄':13,
    '媛':12,'融':16,
}


def _get_radical_client():
    """按需初始化 cnradical。"""
    global _RADICAL_CLIENT
    if _RADICAL_CLIENT is None:
        try:
            from cnradical import Radical, RunOption
            _RADICAL_CLIENT = Radical(RunOption.Radical)
        except Exception as e:
            logger.warning("cnradical 初始化失败: %s", e)
            _RADICAL_CLIENT = False  # 标记失败
    return _RADICAL_CLIENT if _RADICAL_CLIENT is not False else None


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

    # 笔画数（优先用内置字典，零依赖）
    entry["total_strokes"] = str(_STROKE_TABLE.get(char, ""))

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
                # 用内联笔画表填充缓存中笔画数为空的条目
                stroke_filled = 0
                for ch, entry in _CHAR_INFO_CACHE.items():
                    if not entry.get("total_strokes") and ch in _STROKE_TABLE:
                        entry["total_strokes"] = str(_STROKE_TABLE[ch])
                        stroke_filled += 1
                logger.debug("汉字特征缓存已加载: %s (%d 字, 笔画填充 %d 字)",
                             path, len(_CHAR_INFO_CACHE), stroke_filled)
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
    # 优先缓存，其次内联字典
    s1_str = _hc(char_db, c1, "total_strokes")
    s2_str = _hc(char_db, c2, "total_strokes")
    s1 = int(s1_str) if s1_str else _STROKE_TABLE.get(c1, 0)
    s2 = int(s2_str) if s2_str else _STROKE_TABLE.get(c2, 0)
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
        hero_names: 155 名武将名称列表。

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

    def __init__(self, rois: list[list[int]] | None = None, hero_names: list[str] | None = None) -> None:
        self._rois = rois or _DEFAULT_GENERALS_ROI
        self._hero_names = hero_names or []
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

        results: list[dict] = []
        for i, (x, y, w, h) in enumerate(self._rois):
            roi_img = image[y:y + h, x:x + w]
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

                # 矫正返回原文，但 OCR 置信度偏低且存在邻近替代 → 采纳邻近替代
                if conf < _LOW_CONFIDENCE:
                    neighbors = [h for h in self._hero_names
                                 if _levenshtein_distance(text, h) <= _EDIT_DISTANCE_THRESHOLD
                                 and h != text]
                    if neighbors:
                        logger.debug("武将 %d: 置信度(%.4f)偏低，采纳邻近替代 %s", slot, conf, neighbors[0])
                        return neighbors[0], conf

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
