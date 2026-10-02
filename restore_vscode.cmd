@echo off
chcp 65001 >nul
title 修复 Visual Studio Code（d3dcompiler_47.dll）
echo ============================================================
echo  本脚本用于修复损坏的 Visual Studio Code 安装
echo  背景：之前 VSCode 1.137.0 -^> 1.138.0 自动更新被中断，
echo        升级残留导致根目录缺少运行时文件（d3dcompiler_47.dll 等）。
echo  本脚本将：关闭 Code.exe -^> 清理损坏目录 -^> 重装最新版 VSCode
echo  你的 配置/扩展 在 %APPDATA%\Code 和 %USERPROFILE%\.vscode，
echo  不会被删除，重装后自动恢复。
echo ------------------------------------------------------------
echo  请先保存所有工作，然后按任意键开始...
pause >nul

set "ROOT=%LOCALAPPDATA%\Programs\Microsoft VS Code"

echo [1/4] 关闭正在运行的 VS Code...
taskkill /IM Code.exe /F >nul 2>nul
timeout /t 3 /nobreak >nul

echo [2/4] 清理损坏的安装目录...
if exist "%ROOT%" (
    rmdir /s /q "%ROOT%" 2>nul
)
if exist "%ROOT%" (
    echo   [警告] 目录删除不完整，请稍候重试或重启电脑后再运行本脚本。
)

echo [3/4] 安装最新版 Visual Studio Code...
set "SETUP=%TEMP%\VSCodeSetup.exe"
where winget >nul 2>nul
if %errorlevel%==0 (
    echo   使用 winget 安装...
    winget install --id Microsoft.VisualStudioCode -e --silent --accept-package-agreements --accept-source-agreements --force
    if %errorlevel% neq 0 goto :manual
) else (
    goto :manual
)
goto :verify

:manual
echo   使用官方安装包（win32-x64 User 版）...
curl.exe -fSL --retry 3 -o "%SETUP%" "https://update.code.visualstudio.com/latest/win32-x64-user/stable"
if not exist "%SETUP%" (
    echo   [失败] 自动下载失败，请手动下载：
    echo   https://code.visualstudio.com/download
    echo   安装后手动继续。
    pause >nul
    exit /b 1
)
"%SETUP%" /VERYSILENT /NORESTART /MERGETASKS=!runcode
del /q "%SETUP%" >nul 2>nul

:verify
set "ROOT=%LOCALAPPDATA%\Programs\Microsoft VS Code"
echo [4/4] 验证安装...
if exist "%ROOT%\Code.exe" (
    echo   [OK] Code.exe 已就位
) else (
    echo   [失败] 未找到 Code.exe，安装可能未完成。
    pause >nul
    exit /b 1
)
if exist "%ROOT%\d3dcompiler_47.dll" (
    echo   [OK] d3dcompiler_47.dll 已就位（与 Code.exe 同目录）
) else (
    echo   [警告] d3dcompiler_47.dll 未随安装恢复。
    echo   请安装"Microsoft DirectX 最终用户运行时"，或在设置中关闭硬件加速。
)
del /q "%ROOT%\__editor_probe.txt" >nul 2>nul

echo.
echo ============================================================
echo  修复完成！现在可以正常启动 Visual Studio Code。
echo  （若虚拟机里界面仍异常，可在 VSCode 设置里搜索
echo    "disable-hardware-acceleration" 勾选关闭硬件加速）
echo ============================================================
pause >nul
exit /b 0