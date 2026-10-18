# 通讯录处理工具 + 腾讯文档证书下载器 · 一键打包（Windows PowerShell）
# 用法：右键“使用 PowerShell 运行”，或在仓库根目录执行
#   powershell -ExecutionPolicy Bypass -File packaging\build-all.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "==> 安装依赖" -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

Write-Host "==> 打包 通讯录处理工具" -ForegroundColor Cyan
python -m PyInstaller --clean --noconfirm 通讯录处理工具.spec

Write-Host "==> 打包 腾讯文档证书下载器" -ForegroundColor Cyan
python -m PyInstaller --clean --noconfirm 腾讯文档证书下载器.spec

Write-Host "==> 完成，产物在 dist\：" -ForegroundColor Green
Get-ChildItem dist\*.exe | Select-Object Name, @{N="大小(MB)";E={[math]::Round($_.Length/1MB,1)}}
