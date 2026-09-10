@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Fluent Bit Windows OS Log Agent Installer

REM ============================================================
REM Fluent Bit Windows -> Loki Installer
REM ============================================================

REM ============================================================
REM ADMINISTRATOR CHECK
REM More reliable than "net session" on modern Windows
REM ============================================================
whoami /groups | find "S-1-5-32-544" >nul 2>&1
if errorlevel 1 (
    echo.
    echo ============================================================
    echo ERROR: THIS CMD SESSION IS NOT ELEVATED
    echo ============================================================
    echo.
    echo Please do ONE of the following:
    echo.
    echo 1. Search for "Command Prompt"
    echo 2. Right-click Command Prompt
    echo 3. Select "Run as administrator"
    echo 4. Run this script from that Administrator Command Prompt
    echo.
    echo IMPORTANT:
    echo Opening the .CMD file normally is not always elevated,
    echo even if your Windows account is an Administrator.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo        Fluent Bit Windows OS Log Agent Installer
echo ============================================================
echo Running with Administrator privileges.
echo.

REM ============================================================
REM PATH CONFIGURATION
REM ============================================================
set "MASTER_DIR=D:\MASTER"
set "INSTALL_DIR=C:\Program Files\fluent-bit"
set "CONFIG_DIR=C:\ProgramData\FluentBit"
set "CONF_D=%CONFIG_DIR%\conf.d"

REM Fluent Bit state/database stored on D:
set "DATA_DIR=D:\FluentBitData"
set "LOG_DIR=D:\FluentBitLogs"

REM Loki
set "LOKI_HOST=192.168.114.75"
set "LOKI_PORT=80"

REM ============================================================
REM DETECT HOSTNAME
REM ============================================================
for /f %%A in ('hostname') do set "HOST_NAME=%%A"

echo Host detected: %HOST_NAME%
echo.

REM ============================================================
REM SELECT ENVIRONMENT
REM ============================================================
echo ============================================================
echo Select Environment
echo ============================================================
echo 1. Prod
echo 2. Dmz
echo.
choice /c 12 /n /m "Enter choice [1-2]"

if errorlevel 2 (
    set "ENVIRONMENT=dmz"
) else (
    set "ENVIRONMENT=prod"
)

echo Selected Environment: %ENVIRONMENT%
echo.

REM ============================================================
REM SELECT SERVER ROLE
REM ============================================================
echo ============================================================
echo Select Server Role
echo ============================================================
echo 1. App
echo 2. Web
echo 3. Db
echo.
choice /c 123 /n /m "Enter choice [1-3]"

if errorlevel 3 (
    set "SERVER_ROLE=db"
) else if errorlevel 2 (
    set "SERVER_ROLE=web"
) else (
    set "SERVER_ROLE=app"
)

echo Selected Server Role: %SERVER_ROLE%
echo.

REM ============================================================
REM SELECT SITE
REM ============================================================
echo ============================================================
echo Select Site
echo ============================================================
echo 1. Dc1
echo 2. Dc2
echo.
choice /c 12 /n /m "Enter choice [1-2]"

if errorlevel 2 (
    set "SITE=dc2"
) else (
    set "SITE=dc1"
)

echo Selected Site: %SITE%
echo.

REM ============================================================
REM APP GROUP
REM ============================================================
echo ============================================================
echo Enter Application Group
echo ============================================================
echo Example: loki, middleware, banking, database, web
echo.
set /p "APP_GROUP=App Group: "

if "%APP_GROUP%"=="" set "APP_GROUP=default"

echo Selected App Group: %APP_GROUP%
echo.

REM ============================================================
REM DETECT FLUENT BIT INSTALLER
REM ============================================================
echo ============================================================
echo [1/8] Detecting Fluent Bit Installer
echo ============================================================

set "INSTALLER="

for %%F in ("%MASTER_DIR%\fluent-bit*.exe") do (
    if exist "%%F" (
        set "INSTALLER=%%F"
        goto :INSTALLER_FOUND
    )
)

echo.
echo ERROR: Fluent Bit installer not found.
echo Expected example:
echo D:\MASTER\fluent-bit-5.1.1-win64.exe
echo.
pause
exit /b 1

:INSTALLER_FOUND
echo Installer found:
echo %INSTALLER%
echo.

REM ============================================================
REM CREATE DIRECTORIES
REM ============================================================
echo ============================================================
echo [2/8] Creating Directories
echo ============================================================

if not exist "%CONFIG_DIR%" mkdir "%CONFIG_DIR%"
if not exist "%CONF_D%" mkdir "%CONF_D%"
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo Config directory: %CONFIG_DIR%
echo Data directory  : %DATA_DIR%
echo Log directory   : %LOG_DIR%
echo.

REM ============================================================
REM INSTALL FLUENT BIT
REM ============================================================
echo ============================================================
echo [3/8] Installing Fluent Bit
echo ============================================================

if exist "%INSTALL_DIR%\bin\fluent-bit.exe" (
    echo Fluent Bit already installed.
    echo Skipping installer.
) else (
    echo Installing Fluent Bit...
    "%INSTALLER%" /S
    timeout /t 5 /nobreak >nul
)

if not exist "%INSTALL_DIR%\bin\fluent-bit.exe" (
    echo.
    echo ERROR: Fluent Bit executable not found.
    echo Expected:
    echo %INSTALL_DIR%\bin\fluent-bit.exe
    echo.
    pause
    exit /b 1
)

echo Fluent Bit executable found.
echo.

REM ============================================================
REM CREATE WINDOWS EVENT LOG INPUT CONFIG
REM ============================================================
echo ============================================================
echo [4/8] Creating Windows Event Log Configuration
echo ============================================================

(
echo # Windows OS Event Logs
echo # Security channel intentionally excluded
echo.
echo [INPUT]
echo     Name          winlog
echo     Tag           os.windows.system
echo     Channels      System
echo     Interval_Sec  1
echo     DB            D:/FluentBitData/system.db
echo.
echo [INPUT]
echo     Name          winlog
echo     Tag           os.windows.application
echo     Channels      Application
echo     Interval_Sec  1
echo     DB            D:/FluentBitData/application.db
echo.
echo [INPUT]
echo     Name          winlog
echo     Tag           os.windows.setup
echo     Channels      Setup
echo     Interval_Sec  1
echo     DB            D:/FluentBitData/setup.db
) > "%CONF_D%\input-windows-eventlog.conf"

REM ============================================================
REM CREATE FILTER CONFIG
REM ============================================================
echo ============================================================
echo [5/8] Creating Labels and Metadata
echo ============================================================

(
echo [FILTER]
echo     Name          record_modifier
echo     Match         os.windows.*
echo     Record        job os-logs
echo     Record        host %HOST_NAME%
echo     Record        os windows
echo     Record        environment %ENVIRONMENT%
echo     Record        app_group %APP_GROUP%
echo     Record        server_role %SERVER_ROLE%
echo     Record        site %SITE%
echo.
echo [FILTER]
echo     Name          record_modifier
echo     Match         os.windows.system
echo     Record        source system
echo.
echo [FILTER]
echo     Name          record_modifier
echo     Match         os.windows.application
echo     Record        source application
echo.
echo [FILTER]
echo     Name          record_modifier
echo     Match         os.windows.setup
echo     Record        source setup
) > "%CONF_D%\filters.conf"

REM ============================================================
REM CREATE LOKI OUTPUT CONFIG
REM ============================================================
echo ============================================================
echo [6/8] Creating Loki Output Configuration
echo ============================================================

(
echo [OUTPUT]
echo     Name          loki
echo     Match         os.windows.*
echo     Host          %LOKI_HOST%
echo     Port          %LOKI_PORT%
echo     Uri           /loki/api/v1/push
echo     Labels        job=$job,host=$host,os=$os,environment=$environment,app_group=$app_group,server_role=$server_role,site=$site,source=$source
echo     Line_Format   json
) > "%CONF_D%\output-loki.conf"

REM ============================================================
REM CREATE MAIN CONFIG
REM ============================================================
echo ============================================================
echo [7/8] Creating Main Fluent Bit Configuration
echo ============================================================

(
echo [SERVICE]
echo     Flush          5
echo     Daemon         Off
echo     Log_Level      info
echo     Log_File       D:/FluentBitLogs/fluent-bit.log
echo.
echo @INCLUDE conf.d/input-windows-eventlog.conf
echo @INCLUDE conf.d/filters.conf
echo @INCLUDE conf.d/output-loki.conf
) > "%CONFIG_DIR%\fluent-bit.conf"

REM ============================================================
REM VALIDATE CONFIG BEFORE SERVICE START
REM ============================================================
echo.
echo Validating Fluent Bit configuration...
"%INSTALL_DIR%\bin\fluent-bit.exe" -c "%CONFIG_DIR%\fluent-bit.conf" -D
if errorlevel 1 (
    echo.
    echo ERROR: Fluent Bit configuration validation failed.
    echo Review:
    echo %CONFIG_DIR%\fluent-bit.conf
    echo and files under:
    echo %CONF_D%
    echo.
    pause
    exit /b 1
)

REM ============================================================
REM CREATE / RECREATE WINDOWS SERVICE
REM ============================================================
echo ============================================================
echo [8/8] Creating Fluent Bit Service
echo ============================================================

sc stop FluentBit >nul 2>&1
timeout /t 2 /nobreak >nul
sc delete FluentBit >nul 2>&1
timeout /t 2 /nobreak >nul

sc create FluentBit binPath= "\"%INSTALL_DIR%\bin\fluent-bit.exe\" -c \"%CONFIG_DIR%\fluent-bit.conf\"" start= auto DisplayName= "Fluent Bit OS Log Agent"

if errorlevel 1 (
    echo.
    echo ERROR: Failed to create Fluent Bit service.
    pause
    exit /b 1
)

sc description FluentBit "Fluent Bit OS Log Agent - Windows to Loki"

echo.
echo Starting Fluent Bit Service...
sc start FluentBit
timeout /t 5 /nobreak >nul

echo.
echo ============================================================
echo                    INSTALLATION FINISHED
echo ============================================================
echo.
echo Host        : %HOST_NAME%
echo OS          : windows
echo Environment : %ENVIRONMENT%
echo App Group   : %APP_GROUP%
echo Server Role : %SERVER_ROLE%
echo Site        : %SITE%
echo.
echo Loki Endpoint:
echo http://%LOKI_HOST%:%LOKI_PORT%/loki/api/v1/push
echo.
echo Config:
echo %CONFIG_DIR%\fluent-bit.conf
echo.
echo Data:
echo %DATA_DIR%
echo.
echo Fluent Bit Log:
echo %LOG_DIR%\fluent-bit.log
echo.
echo Checking Fluent Bit Service...
echo.
sc query FluentBit
echo.
pause

endlocal
exit /b 0