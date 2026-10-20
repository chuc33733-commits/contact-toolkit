# -*- coding: utf-8 -*-
"""
配置文件
"""

# 应用程序配置
APP_NAME = "通讯录处理工具"
APP_VERSION = "1.1.1"
WINDOW_WIDTH = 1080
WINDOW_HEIGHT = 680

# 应用程序配置字典
APP_CONFIG = {
    'name': APP_NAME,
    'version': APP_VERSION,
    'window_width': WINDOW_WIDTH,
    'window_height': WINDOW_HEIGHT
}

# 文件处理配置
# 修复：界面文件对话框早已允许选择 .xlsm，但此处缺失导致实际导入报
# "不支持的文件格式: .xlsm"；这里补齐 .xlsm，使界面与实现一致。
SUPPORTED_EXCEL_FORMATS = ['.xlsx', '.xlsm', '.xls']
SUPPORTED_VCF_FORMATS = ['.vcf']
DEFAULT_ENCODING = 'utf-8'
FALLBACK_ENCODINGS = ['gbk', 'gb2312', 'latin1']

# Excel列标题映射（值与 vCard 属性一一对应）
EXCEL_COLUMN_MAPPING = {
    'name': ['name', 'fullname', 'full_name', '姓名', '名字', '联系人', 'contact', '姓 名'],
    'nickname': ['nickname', 'alias', 'aka', '昵称', '别名', '别称', '小名', '艺名', '曾用名'],
    'phone': ['phone', 'mobile', 'tel', 'telephone', 'cell', '电话', '手机', '手机号',
              '手机号码', '联系电话', '电话号码', '固话', '座机'],
    'email': ['email', 'e-mail', 'mail', '邮箱', '电子邮件', '邮件', '邮件地址', '电子邮箱'],
    'company': ['company', 'organization', 'org', '单位', '公司', '公司名称', '组织',
                '工作单位', '企业'],
    'title': ['title', 'position', 'job', '职位', '职务', '岗位', '头衔'],
    'address': ['address', 'addr', '地址', '住址', '联系地址', '通讯地址', '家庭住址'],
    'birthday': ['birthday', 'birth', 'birthdate', 'dateofbirth', '生日', '公历生日',
                 '阳历生日', '出生日期', '出生年月日', '出生年月'],
    'lunar_birthday': ['lunar_birthday', 'lunar', 'lunarbirthday', 'lunar_birth',
                       '农历生日', '阴历生日', '旧历生日', '农历', '阴历', '旧历'],
    'photo': ['photo', 'avatar', 'picture', 'image', 'photo_uri', '照片', '头像',
              '图片', '相片', '照片路径', '头像路径'],
    'website': ['website', 'web', 'url', 'homepage', 'blog', '网站', '网址', '主页',
                '个人主页', '个人网站'],
    'notes': ['notes', 'note', 'remark', 'memo', 'comment', '备注', '说明', '注释', '备注信息'],
    'categories': ['categories', 'category', 'group', 'tag', 'tags', '分类', '分组',
                   '标签', '群组', '组别'],
}

# VCF字段映射
VCF_FIELD_MAPPING = {
    'FN': 'name',
    'NICKNAME': 'nickname',
    'TEL': 'phone',
    'EMAIL': 'email',
    'ORG': 'company',
    'TITLE': 'title',
    'ADR': 'address',
    'BDAY': 'birthday',
    'X-ALTBDAY': 'lunar_birthday',
    'X-BIRTHDAY-LUNAR': 'lunar_birthday',
    'X-LUNAR-BIRTHDAY': 'lunar_birthday',
    'X-NONGGREGORIAN-BIRTHDAY': 'lunar_birthday',
    'URL': 'website',
    'PHOTO': 'photo',
    'NOTE': 'notes',
    'CATEGORIES': 'categories',
}

# 农历生日在 vCard 中常见的属性名（Android 通讯录用 X-ALTBDAY，部分工具用 X-BIRTHDAY-LUNAR）
LUNAR_BIRTHDAY_VCARD_KEYS = (
    'X-ALTBDAY',
    'X-BIRTHDAY-LUNAR',
    'X-LUNAR-BIRTHDAY',
    'X-NONGGREGORIAN-BIRTHDAY',
)

# 由程序负责生成/合并的 vCard 属性；其余属性（如 IMPP、X-PHONETIC-*、X-SOCIALPROFILE）
# 一律作为"未建模字段"原样保留，确保合并与导出时不丢信息。
VCF_GENERATED_PROPERTIES = frozenset({
    'BEGIN', 'END', 'VERSION', 'PRODID', 'REV',
    'FN', 'N', 'NICKNAME', 'TEL', 'EMAIL', 'ORG', 'TITLE', 'ADR',
    'BDAY', 'URL', 'PHOTO', 'NOTE', 'CATEGORIES',
}) | frozenset(LUNAR_BIRTHDAY_VCARD_KEYS)

# RFC 2425 折行宽度：导出的长行（照片 base64 等）按此宽度折行，续行以单个空格开头
VCARD_FOLD_WIDTH = 75

# 照片处理配置
PHOTO_MAX_BYTES = 5 * 1024 * 1024          # 单张照片最大 5MB，超出则跳过并提示
PHOTO_URI_SCHEMES = ('http://', 'https://')
EXCEL_CELL_MAX_CHARS = 32000               # Excel 单元格字符上限（略低于 32767）

# 电话号码标准化配置
PHONE_CLEANUP_PATTERNS = [
    r'[^\d+\-\(\)\s]',  # 移除非数字、+、-、()、空格的字符
    r'\s+',             # 多个空格替换为单个空格
]

# 合并配置
MERGE_SIMILARITY_THRESHOLD = 0.8  # 姓名相似度阈值
PHONE_MATCH_THRESHOLD = 7         # 电话号码匹配的最少位数
MERGE_NOTE_SEPARATOR = '; '       # 合并备注时的分隔符
CN_MOBILE_LENGTH = 11             # 中国大陆手机号位数（用于"11位号码相同"的判定）
