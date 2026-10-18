# -*- coding: utf-8 -*-
"""Excel 导入模板生成（内存版，供 /api/template/excel 下载）。

字段与桌面版 generate_excel_template.py 保持一致（13 列），
但照片列示例改为「链接 / base64 / 留空」的 Web 可行写法。
"""

import io

import pandas as pd

_COLUMNS = [
    '姓名', '昵称', '电话', '邮箱', '公司', '职位', '地址',
    '生日', '农历生日', '照片', '网站', '备注', '分类',
]

_ROWS = [
    ['张三', '小三, 老张', '13800138000, 010-12345678', 'zhangsan@example.com',
     '某某公司', '软件工程师', '北京市朝阳区建国门外大街1号', '1990-01-15', '1989-12-08',
     'https://example.com/avatar/zhangsan.jpg', 'https://zhangsan.example.com', '重要客户', '客户, 重要'],
    ['李四', '四哥', '139-0013-9000', 'lisi@example.com', '某某科技', '产品经理',
     '深圳市南山区科技园南区', '1985年6月20日', '农历五月初五', '', '', '技术合作伙伴', '合作伙伴'],
    ['王五', '', '+86 137 0013 7000', '', '某某公司', '数据分析师', '上海市浦东新区张江高科技园区',
     '1992/03/10', '--0415', '', 'https://wangwu.example.com', '老同学', '同学'],
    ['赵六', '六六', '13600136000', 'zhaoliu@example.com', '某某集团', '项目经理',
     '杭州市西湖区文三路', '19881205', '1987-07-07', '', '', '业务联系人', '客户'],
]


def build_template_bytes() -> bytes:
    """返回「全字段」导入模板的 xlsx 字节流。"""
    df = pd.DataFrame(_ROWS, columns=_COLUMNS)
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False, engine='openpyxl')
    return buffer.getvalue()
