# 通讯录处理工具 + 腾讯文档证书下载器

两个自用的桌面小工具，源码与网页版放在同一个仓库里：

| 工具 | 做什么 | 网页版 | 桌面版 |
|---|---|---|---|
| **通讯录处理工具** | Excel ↔ VCF 互转、多个 VCF 合并去重、照片与农历生日保留 | `/contacts` | `dist/通讯录处理工具.exe` |
| **腾讯文档证书下载器** | 从腾讯文档智能表格/收集表批量下载证书、附件，按姓名+身份证号重命名 | `/cert` | `dist/腾讯文档证书下载器.exe` |

网页版是同一个仓库里的 FastAPI 薄适配层（`线上版/`），业务逻辑与桌面版共用；
部署后一个端口同时提供主页、通讯录工具和证书下载器。

## 快速开始（桌面版）

1. 安装 Python 3.10+（安装时勾选 “Add Python to PATH”）。
2. 安装依赖并运行：

```bash
pip install -r requirements.txt
python main.py                       # 通讯录处理工具
python certdown/cert_gui.py          # 腾讯文档证书下载器
```

3. 打包 exe（Windows）：

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build-all.ps1
# 或分别执行：
pyinstaller --clean --noconfirm 通讯录处理工具.spec
pyinstaller --clean --noconfirm 腾讯文档证书下载器.spec
```

产物在 `dist/`：单文件、双击即用，目标机器无需安装 Python。

## 通讯录处理工具

- **Excel → VCF**：支持 `.xlsx/.xls/.xlsm`，13 个字段（姓名/昵称/电话/邮箱/公司/职位/地址/
  生日/农历生日/照片/网站/备注/分类），列名支持中英文多种写法；照片支持本地路径、网络链接、
  base64 三种写法。
- **多号码导入**：电话支持多列（电话1/电话2、手机/固话…）自动合并，一格里也可用逗号/顿号/换行
  填写多个号码，全部导入不丢失。
- **VCF → Excel**：导出 13 列；含照片时自动打 zip（Excel + 照片目录）。
- **VCF 合并去重**：按电话/邮箱/姓名多维度判定；信息少的并入信息多的，列表字段取并集，
  冲突值写入备注留痕；未建模的 vCard 属性（IMPP、X-PHONETIC-* 等）原样保留。
- 字段写法、照片填法、日期归一规则见 [`Excel导入模板使用说明.md`](Excel导入模板使用说明.md)。

## 腾讯文档证书下载器

- 读取腾讯文档智能表格（`docs.qq.com/smartsheet/...`）的记录，批量换取附件临时下载地址。
- 附件范围可选 **仅图片** 或 **全部文件**（PDF / Word / Excel / 压缩包等）。
- 文件名支持 10 种预设 + 自定义模板（`{name}` `{id}` `{id_last4}` `{title}` `{datetime}` …），
  界面实时预览。
- 下载报告支持 csv / xlsx / json / jsonl / txt / md / tsv。
- 私有文档需在界面里填浏览器 Cookie；签名地址有效期短，换取后应立即下载。
- 命令行同样可用（适合批处理）：

```bash
python certdown/cert_downloader.py --doc <文档链接或ID> -o ./证书 --kinds all
python certdown/cert_downloader.py --list-vars          # 查看命名变量与预设
```

## 网页版部署

```bash
docker compose up -d --build      # 镜像 contact-web，端口 8788
```

- 单进程内存会话（关页不留存），必须 `workers=1`；容器只读根 + `/var/scratch` 绑定挂载。
- 二级路径部署：`BASE_PATH=/tongxunlu`，服务端剥前缀，前端用 `window.__BASE__` 拼地址。
- 临时区按「附件总量 × 2」估容量；`SCRATCH_DIR` 指向磁盘目录，不要用 tmpfs。
- 证书模块的 PDF 合并是零依赖流式拼装（`线上版/app/cert_pdf.py`），不重编码 JPEG。

## 目录结构

```
main.py                 通讯录工具入口（tkinter）
gui/ models/ processors/ utils/ config/   通讯录工具业务代码
certdown/               证书下载器内核 + 图形界面（cert_gui.py）+ 单元测试
线上版/                  FastAPI 网页层（主页 /contacts /cert）
Dockerfile compose.yaml 网页版容器
*.spec packaging/           PyInstaller 打包配置与脚本
```

## 测试

```bash
python -m pytest certdown/tests -q          # 命名 / 导出层 / 附件类型
python tests/run_tests.py                   # 通讯录工具单元测试
```

> `tests/` 里部分用例依赖真实 VCF 样本（含个人信息，未入库），缺少样本时会自动跳过或报“文件不存在”，
> 不影响代码本身。仓库只保留可公开的代码与单元测试。

## 说明

- 证书下载器依赖腾讯文档未公开的内部接口，官方不承诺稳定；接口失效时优先检查
  `dop-api/opendoc` 是否仍返回 `clientVars.collab_client_vars`。
- 工具只读取文档内容，不上传、不改写任何数据；Cookie 只保存在本机内存/配置文件中。
- 本仓库不含任何真实联系人或证书数据。
