rem@echo off
rem --- 
rem ---  exeを生成
rem --- 

rem ---  カレントディレクトリを実行先に変更
cd /d %~dp0

cls

activate vmdsizing_np && cd src && python translate.py && cd .. && activate vmdsizing_cython && src\setup_install.bat && pyinstaller --clean pmx_tailor.spec && copy /y archive\Readme.txt dist\Readme.txt && copy /y archive\β版Readme.txt dist\β版Readme.txt
rem activate vmdsizing_cython && pyinstaller --clean pmx_tailor.spec
