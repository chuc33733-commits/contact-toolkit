# -*- coding: utf-8 -*-
"""
生成"全字段"Excel 导入模板。

输出：
- 联系人导入模板_全字段.xlsx  —— 覆盖全部可导入字段（13 列）
- 示例照片/*.png               —— 供"照片"列演示相对路径写法

注意：本脚本不会覆盖 联系人导入模板.xlsx（该文件里是真实通讯录数据）。
"""

import os
import struct
import zlib

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_XLSX = os.path.join(BASE_DIR, '联系人导入模板_全字段.xlsx')
PHOTO_DIR = os.path.join(BASE_DIR, '示例照片')


def write_png(path: str, width: int, height: int, color=(37, 99, 235)) -> None:
    """
    生成一张纯色 PNG（不依赖 Pillow，保证模板脚本零额外依赖）。

    Args:
        path: 输出路径
        width: 宽度
        height: 高度
        color: RGB 颜色
    """
    raw = b''.join(b'\x00' + bytes(color) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        payload = struct.pack('>I', len(data)) + tag + data
        return payload + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(raw, 9))
    png += chunk(b'IEND', b'')

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as fh:
        fh.write(png)


def build_template() -> pd.DataFrame:
    """
    构造模板数据（每行演示不同的字段写法）。

    Returns:
        模板 DataFrame
    """
    data = {
        '姓名': ['张三', '李四', '王五', '赵六', '钱七', '孙八'],
        '昵称': ['小三, 老张', '四哥', '', '六六', '', '阿八; 小八'],
        '电话': [
            '13800138000, 010-12345678',   # 一格里多个号码
            '139-0013-9000',
            '+86 137 0013 7000',           # 带国家码
            '13600136000',
            '13500135000；13500135001',     # 中文分号分隔
            '13300133000',
        ],
        '邮箱': ['zhangsan@example.com', 'lisi@example.com', '', 'zhaoliu@example.com', '', 'sunba@example.com'],
        '公司': ['阿里巴巴集团', '腾讯科技', '百度公司', '字节跳动', '华为技术有限公司', '内江师范学院'],
        '职位': ['软件工程师', '产品经理', '数据分析师', '项目经理', '技术总监', '学生'],
        '地址': ['北京市朝阳区建国门外大街1号', '深圳市南山区科技园南区', '上海市浦东新区张江高科技园区',
                 '杭州市西湖区文三路', '广州市天河区珠江新城', '四川省内江市东兴区'],
        '生日': ['1990-01-15', '1985年6月20日', '1992/03/10', '19881205', '1991-09-25', '1998-04-15'],
        '农历生日': ['1989-12-08', '农历五月初五', '--0415', '1987-07-07', '', '1998-03-20'],
        # 照片三列写法：相对路径 / 网络链接 / 留空
        '照片': ['示例照片/张三.png', 'https://example.com/avatar/lisi.jpg', '',
                 '示例照片/赵六.png', '', ''],
        '网站': ['https://zhangsan.example.com', '', 'https://wangwu.example.com',
                 '', 'https://qianqi.example.com', ''],
        '备注': ['重要客户', '技术合作伙伴', '老同学', '业务联系人', '朋友推荐', '在校学生'],
        '分类': ['客户, 重要', '合作伙伴', '同学', '客户', '朋友', '学生, 校友'],
    }
    return pd.DataFrame(data)


def main() -> None:
    """生成模板文件与示例照片"""
    write_png(os.path.join(PHOTO_DIR, '张三.png'), 96, 96, (37, 99, 235))
    write_png(os.path.join(PHOTO_DIR, '赵六.png'), 96, 96, (220, 38, 38))

    df = build_template()
    df.to_excel(OUTPUT_XLSX, index=False, engine='openpyxl')

    print(f"Excel模板文件已生成: {OUTPUT_XLSX}")
    print(f"示例照片目录: {PHOTO_DIR}")
    print(f"包含 {len(df)} 行示例数据，{len(df.columns)} 个字段")


if __name__ == '__main__':
    main()
