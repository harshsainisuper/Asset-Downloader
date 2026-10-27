@echo off
rem Same as start-windows.bat, plus a free public https://....trycloudflare.com URL.
rem Set ACCESS_PASSWORD in the .env file first: the URL can be opened by anyone.
call "%~dp0start-windows.bat" --tunnel
