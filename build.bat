@echo off
REM Tek dosyalik OyunCeviri.exe olusturur -> dist\OyunCeviri.exe
pip install -r requirements.txt
pyinstaller --noconfirm --onefile --noconsole --uac-admin --name OyunCeviri --collect-all winocr --collect-submodules winrt --collect-binaries winrt oyun_ceviri.py
echo.
echo Bitti! dist\OyunCeviri.exe dosyasina cift tiklayarak calistirabilirsin.
pause
