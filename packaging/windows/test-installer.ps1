[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$InstallerPath,
    [Parameter(Mandatory)]
    [string]$WorkingRoot
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$working = [System.IO.Path]::GetFullPath($WorkingRoot)
$installRoot = Join-Path $working "installed"
$installLog = Join-Path $working "install.log"
$uninstallLog = Join-Path $working "uninstall.log"
$shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\SKYWriter Prototype\SKYWriter Prototype.lnk"
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{C67A9057-226E-4BB8-9437-31A092733D88}_is1"

if ((Test-Path -LiteralPath $uninstallKey) -or (Test-Path -LiteralPath $shortcut)) {
    throw "A per-user SKYWriter Prototype install already exists; refusing to replace it during smoke testing."
}

New-Item -ItemType Directory -Force -Path $working | Out-Null
$installArguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/DIR=$installRoot",
    "/MERGETASKS=!desktopicon",
    "/LOG=$installLog"
)
$application = Join-Path $installRoot "SKYWriter.exe"
$uninstaller = Join-Path $installRoot "unins000.exe"
try {
$process = Start-Process -FilePath $installer -ArgumentList $installArguments -Wait -PassThru -WindowStyle Hidden
if ($process.ExitCode -ne 0) {
    throw "Installer smoke install failed with exit code $($process.ExitCode)."
}

if (-not (Test-Path -LiteralPath $application -PathType Leaf)) {
    throw "Installed application is missing: $application"
}
$serialImport = Start-Process -FilePath $application -ArgumentList "--packaged-serial-import-smoke" -Wait -PassThru -WindowStyle Hidden
if ($serialImport.ExitCode -ne 0) {
    throw "Packaged Windows serial-enumeration runtime import failed with exit code $($serialImport.ExitCode)."
}
if (-not (Test-Path -LiteralPath $shortcut -PathType Leaf)) {
    throw "Start-menu shortcut is missing: $shortcut"
}

$previousEvidence = $env:SKYWRITER_PACKAGED_SMOKE_EVIDENCE
$previousScreenshot = $env:SKYWRITER_PACKAGED_SMOKE_SCREENSHOT
$previousTileOrigin = $env:SKYWRITER_PACKAGED_SMOKE_TILE_ORIGIN
$smokeEvidence = Join-Path $working "packaged-map-smoke.json"
$smokeScreenshot = Join-Path $working "packaged-map-smoke.png"
$fixtureTile = Join-Path $working "controlled-tile.png"
$tileStopSignal = Join-Path $working "controlled-tile-server.stop"
$tileServer = $null
try {
    Remove-Item -LiteralPath $tileStopSignal -Force -ErrorAction SilentlyContinue
    Add-Type -AssemblyName System.Drawing
    $tileBitmap = [System.Drawing.Bitmap]::new(256, 256)
    $tileGraphics = [System.Drawing.Graphics]::FromImage($tileBitmap)
    $tileBrush = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::FromArgb(32, 170, 110))
    $tilePen = [System.Drawing.Pen]::new([System.Drawing.Color]::White, 10)
    try {
        $tileGraphics.FillRectangle($tileBrush, 0, 0, 256, 256)
        $tileGraphics.DrawLine($tilePen, 0, 0, 256, 256)
        $tileGraphics.DrawLine($tilePen, 256, 0, 0, 256)
        $tileBitmap.Save($fixtureTile, [System.Drawing.Imaging.ImageFormat]::Png)
    }
    finally {
        $tilePen.Dispose()
        $tileBrush.Dispose()
        $tileGraphics.Dispose()
        $tileBitmap.Dispose()
    }

    $portProbe = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Loopback,
        0
    )
    $portProbe.Start()
    $tilePort = ([System.Net.IPEndPoint]$portProbe.LocalEndpoint).Port
    $portProbe.Stop()
    $tileBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($fixtureTile))
    $tileServer = Start-Job -ArgumentList $tilePort, $tileBase64, $tileStopSignal -ScriptBlock {
        param([int]$Port, [string]$TileBase64, [string]$StopSignal)
        $tile = [Convert]::FromBase64String($TileBase64)
        $listener = [System.Net.Sockets.TcpListener]::new(
            [System.Net.IPAddress]::Loopback,
            $Port
        )
        $listener.Start()
        try {
            while (-not (Test-Path -LiteralPath $StopSignal)) {
                if (-not $listener.Pending()) {
                    Start-Sleep -Milliseconds 50
                    continue
                }
                $client = $listener.AcceptTcpClient()
                try {
                    $stream = $client.GetStream()
                    $reader = [IO.StreamReader]::new(
                        $stream,
                        [Text.Encoding]::ASCII,
                        $false,
                        1024,
                        $true
                    )
                    while (($line = $reader.ReadLine()) -ne $null -and $line.Length -gt 0) {}
                    $headers = [Text.Encoding]::ASCII.GetBytes(
                        "HTTP/1.1 200 OK`r`n" +
                        "Content-Type: image/png`r`n" +
                        "Cache-Control: public, max-age=300`r`n" +
                        "Content-Length: $($tile.Length)`r`n" +
                        "Connection: close`r`n`r`n"
                    )
                    $stream.Write($headers, 0, $headers.Length)
                    $stream.Write($tile, 0, $tile.Length)
                    $stream.Flush()
                    $reader.Dispose()
                    $stream.Dispose()
                }
                finally {
                    $client.Dispose()
                }
            }
        }
        finally {
            $listener.Stop()
        }
    }
    Start-Sleep -Milliseconds 500

    $env:SKYWRITER_PACKAGED_SMOKE_EVIDENCE = $smokeEvidence
    $env:SKYWRITER_PACKAGED_SMOKE_SCREENSHOT = $smokeScreenshot
    $env:SKYWRITER_PACKAGED_SMOKE_TILE_ORIGIN = "http://127.0.0.1:$tilePort"
    Push-Location $env:SystemRoot
    try {
        $smoke = Start-Process -FilePath $application -ArgumentList "--packaged-map-visual-smoke" -Wait -PassThru
    }
    finally {
        Pop-Location
    }
    if ($smoke.ExitCode -ne 0) {
        throw "Packaged application launch smoke failed with exit code $($smoke.ExitCode)."
    }
    if (-not (Test-Path -LiteralPath $smokeEvidence -PathType Leaf)) {
        throw "Packaged application did not write mounted-map smoke evidence."
    }
    $mapEvidence = Get-Content -LiteralPath $smokeEvidence -Raw | ConvertFrom-Json
    if (
        -not $mapEvidence.ready -or
        -not $mapEvidence.visual_ready -or
        $mapEvidence.leaflet_version -ne "1.9.4" -or
        $mapEvidence.container_width_px -le 0 -or
        $mapEvidence.container_height_px -le 0 -or
        -not $mapEvidence.map_document_exists -or
        $mapEvidence.provider -ne "openstreetmap" -or
        $mapEvidence.provider_state -ne "online" -or
        $mapEvidence.loaded_tiles -le 0 -or
        $mapEvidence.error_tiles -ne 0 -or
        $mapEvidence.pending_tiles -ne 0 -or
        -not $mapEvidence.leaflet_controls_dom -or
        -not $mapEvidence.leaflet_controls_visual -or
        $mapEvidence.fixture_pixel_ratio -lt 0.10 -or
        $mapEvidence.non_black_pixel_ratio -lt 0.50 -or
        $mapEvidence.renderer.mode -ne "chromium-software" -or
        -not $mapEvidence.renderer.windows_software_default -or
        -not $mapEvidence.renderer.chromium_gpu_disabled -or
        $mapEvidence.renderer.sandbox_disabled_by_skywriter -or
        $mapEvidence.renderer.sandbox_disabled_by_environment -or
        -not $mapEvidence.vehicle_io_blocked
    ) {
        throw "Packaged application rendered-map evidence failed validation."
    }
    if (-not (Test-Path -LiteralPath $smokeScreenshot -PathType Leaf)) {
        throw "Packaged application did not capture the rendered map surface."
    }
}
finally {
    $env:SKYWRITER_PACKAGED_SMOKE_EVIDENCE = $previousEvidence
    $env:SKYWRITER_PACKAGED_SMOKE_SCREENSHOT = $previousScreenshot
    $env:SKYWRITER_PACKAGED_SMOKE_TILE_ORIGIN = $previousTileOrigin
    if ($null -ne $tileServer) {
        [IO.File]::WriteAllText($tileStopSignal, "stop")
        $stoppedTileServer = Wait-Job -Job $tileServer -Timeout 10
        if ($null -eq $stoppedTileServer) {
            throw "Controlled tile server did not stop after its signal."
        }
        Receive-Job -Job $tileServer -ErrorAction SilentlyContinue | Out-Null
        Remove-Job -Job $tileServer -ErrorAction SilentlyContinue
    }
    Remove-Item -LiteralPath $tileStopSignal -Force -ErrorAction SilentlyContinue
}

$shortcutShell = New-Object -ComObject WScript.Shell
$shortcutTarget = $shortcutShell.CreateShortcut($shortcut).TargetPath
if ([System.IO.Path]::GetFullPath($shortcutTarget) -ne [System.IO.Path]::GetFullPath($application)) {
    throw "Start-menu shortcut does not target the exact installed executable: $shortcutTarget"
}
$previousAcceptanceMode = $env:SKYWRITER_PACKAGED_UI_ACCEPTANCE
$previousAcceptanceEvidence = $env:SKYWRITER_INSTALLED_UI_EVIDENCE
$acceptanceRoot = Join-Path $working "installed-ui-acceptance"
try {
    $env:SKYWRITER_PACKAGED_UI_ACCEPTANCE = "1"
    $env:SKYWRITER_INSTALLED_UI_EVIDENCE = $acceptanceRoot
    Push-Location $env:SystemRoot
    try {
        $acceptance = Start-Process -FilePath $shortcut -Wait -PassThru
    }
    finally {
        Pop-Location
    }
    $acceptanceEvidencePath = Join-Path $acceptanceRoot "installed-ui-acceptance.json"
    if ($acceptance.ExitCode -ne 0) {
        if (Test-Path -LiteralPath $acceptanceEvidencePath -PathType Leaf) {
            Write-Host (Get-Content -LiteralPath $acceptanceEvidencePath -Raw)
        }
        throw "Installed UI acceptance failed with exit code $($acceptance.ExitCode)."
    }
    if (-not (Test-Path -LiteralPath $acceptanceEvidencePath -PathType Leaf)) {
        throw "Installed shortcut launch did not write UI acceptance evidence."
    }
    $acceptanceEvidence = Get-Content -LiteralPath $acceptanceEvidencePath -Raw | ConvertFrom-Json
    if (
        -not $acceptanceEvidence.passed -or
        -not $acceptanceEvidence.hardware_block_environment -or
        $acceptanceEvidence.provider -ne "offline" -or
        $acceptanceEvidence.vehicle_io.attempts -ne 0 -or
        $acceptanceEvidence.vehicle_io.successes -ne 0 -or
        $acceptanceEvidence.serial_selection.enumerated_count -ne 1 -or
        $acceptanceEvidence.serial_selection.enumerated_label -notmatch "COM42.*installed-acceptance serial fixture" -or
        $acceptanceEvidence.serial_selection.auto_selected -or
        $acceptanceEvidence.serial_selection.usb_default_baudrate -ne 115200 -or
        $acceptanceEvidence.serial_selection.selected_link_kind -ne "sik" -or
        $acceptanceEvidence.serial_selection.sik_default_baudrate -ne 57600 -or
        $acceptanceEvidence.serial_selection.vehicle_open_clicked -or
        $acceptanceEvidence.screenshots.Count -lt 10 -or
        $acceptanceEvidence.tab_navigation.Count -ne 3
    ) {
        throw "Installed UI acceptance evidence failed validation."
    }
}
finally {
    $env:SKYWRITER_PACKAGED_UI_ACCEPTANCE = $previousAcceptanceMode
    $env:SKYWRITER_INSTALLED_UI_EVIDENCE = $previousAcceptanceEvidence
}
}
finally {
$uninstallArguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/LOG=$uninstallLog"
)
if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
    $uninstall = Start-Process -FilePath $uninstaller -ArgumentList $uninstallArguments -Wait -PassThru -WindowStyle Hidden
    if ($uninstall.ExitCode -ne 0) {
        throw "Installer smoke uninstall failed with exit code $($uninstall.ExitCode)."
    }
}
if (Test-Path -LiteralPath $application) {
    throw "Application remained after uninstall: $application"
}
if (Test-Path -LiteralPath $shortcut) {
    throw "Start-menu shortcut remained after uninstall: $shortcut"
}
}

Write-Host "Install, shortcut-launched installed UI acceptance, deterministic non-black map surface, hardware blocking, and uninstall passed."
