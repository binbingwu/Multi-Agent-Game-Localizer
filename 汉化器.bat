@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=%~dp0venv\Scripts\python.exe
if not exist "%PY%" (
  echo [首次运行] 正在创建 Python 环境...
  python -m venv venv || (echo 需要先安装 Python 3.10+ & pause & exit /b 1)
  "%PY%" -m pip install -q pillow
)

:menu
echo.
echo ================= AI 游戏汉化系统 =================
echo  1. 检查/下载本地模型和推理引擎 (setup)
echo  2. 新建汉化项目 (init)
echo  3. 运行完整汉化流程 (提取-上下文-翻译-质检-打包)
echo  4. 查看进度 (status)
echo  5. 打包并安装补丁到游戏
echo  6. 卸载补丁（恢复原版）
echo  7. 导出需人工复核的条目 (review.tsv)
echo  8. 导入人工修改后的条目
echo  9. 冒烟测试（启动游戏截图）
echo  0. 退出
echo ===================================================
set /p C=请选择:
if "%C%"=="1" "%PY%" -m hanhua setup
if "%C%"=="2" (
  set /p N=项目名称（英文，如 Evenicle）:
  set /p D=游戏目录:
  call "%PY%" -m hanhua init "%%N%%" "%%D%%"
)
if "%C%"=="3" (set /p N=项目名称: & call "%PY%" -m hanhua run "%%N%%")
if "%C%"=="4" (set /p N=项目名称: & call "%PY%" -m hanhua status "%%N%%")
if "%C%"=="5" (set /p N=项目名称: & call "%PY%" -m hanhua build "%%N%%" --install)
if "%C%"=="6" (set /p N=项目名称: & call "%PY%" -m hanhua uninstall "%%N%%")
if "%C%"=="7" (set /p N=项目名称: & call "%PY%" -m hanhua export "%%N%%")
if "%C%"=="8" (
  set /p N=项目名称:
  set /p F=TSV 文件路径:
  call "%PY%" -m hanhua import "%%N%%" "%%F%%"
)
if "%C%"=="9" (set /p N=项目名称: & call "%PY%" -m hanhua test "%%N%%")
if "%C%"=="0" exit /b 0
goto menu
