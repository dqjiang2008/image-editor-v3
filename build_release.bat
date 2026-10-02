@echo off
chcp 65001 >nul
echo ========================================
echo   AI Video Production Studio 打包工具
echo ========================================
echo.

:: 设置版本号
set VERSION=2.4.2
set BUILD_DIR=build\release-v%VERSION%

echo [1/6] 清理旧的构建目录...
if exist "%BUILD_DIR%" (
    rmdir /s /q "%BUILD_DIR%"
)

echo [2/6] 创建构建目录...
mkdir "%BUILD_DIR%"
mkdir "%BUILD_DIR%\image-editor-v3"

echo [3/6] 复制源代码...
xcopy /E /I /Y /Q src "%BUILD_DIR%\image-editor-v3\src"
xcopy /E /I /Y /Q data "%BUILD_DIR%\image-editor-v3\data"

echo [4/6] 复制配置文件...
copy requirements.txt "%BUILD_DIR%\image-editor-v3\"
copy run.bat "%BUILD_DIR%\image-editor-v3\"
copy README.md "%BUILD_DIR%\image-editor-v3\"
copy LICENSE "%BUILD_DIR%\image-editor-v3\"
copy CHANGELOG.md "%BUILD_DIR%\image-editor-v3\"

echo [5/6] 清理敏感数据...
:: 清空 API Key
echo {"api_key": "", "api_base_url": "https://api.agnes-ai.cn"} > "%BUILD_DIR%\image-editor-v3\data\config.json"
:: 删除测试项目数据
if exist "%BUILD_DIR%\image-editor-v3\data\projects" (
    rmdir /s /q "%BUILD_DIR%\image-editor-v3\data\projects"
)
:: 删除日志
if exist "%BUILD_DIR%\image-editor-v3\data\logs" (
    rmdir /s /q "%BUILD_DIR%\image-editor-v3\data\logs"
)

echo [6/6] 创建压缩包...
cd "%BUILD_DIR%"
powershell -Command "Compress-Archive -Path image-editor-v3 -DestinationPath ..\..\image-editor-v3-v%VERSION%.zip -Force"
cd ..\..

echo.
echo ========================================
echo   打包完成！
echo   输出文件: image-editor-v3-v%VERSION%.zip
echo ========================================
echo.
pause