# Project Cleanup Script
# Run this before commits or releases to remove junk files

Write-Host "🧹 Starting project cleanup..." -ForegroundColor Cyan

# 1. Remove Python cache
Write-Host "`n1. Removing Python cache..." -ForegroundColor Yellow
Get-ChildItem -Path "." -Recurse -Directory -Filter "__pycache__" | ForEach-Object {
    Remove-Item -Path $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
}
Get-ChildItem -Path "." -Recurse -Filter "*.pyc" | ForEach-Object {
    Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
}
Write-Host "✅ Python cache removed" -ForegroundColor Green

# 2. Remove build artifacts
Write-Host "`n2. Removing build artifacts..." -ForegroundColor Yellow
if (Test-Path "build\RichReviewTool") {
    Remove-Item -Path "build\RichReviewTool" -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host "✅ Build artifacts removed" -ForegroundColor Green
} else {
    Write-Host "⏭️  No build artifacts found" -ForegroundColor Gray
}

# 3. Remove dist folder
Write-Host "`n3. Checking dist folder..." -ForegroundColor Yellow
if (Test-Path "dist") {
    $distSize = (Get-ChildItem -Path "dist" -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
    Write-Host "⚠️  dist/ folder exists ($([math]::Round($distSize, 2)) MB)" -ForegroundColor Magenta
    $response = Read-Host "Delete dist/ folder? (y/n)"
    if ($response -eq "y") {
        Remove-Item -Path "dist" -Recurse -Force
        Write-Host "✅ Dist folder removed" -ForegroundColor Green
    } else {
        Write-Host "⏭️  Skipped dist/ deletion" -ForegroundColor Gray
    }
} else {
    Write-Host "✅ No dist folder" -ForegroundColor Green
}

# 4. Clean test cache files
Write-Host "`n4. Cleaning test cache..." -ForegroundColor Yellow
$testFiles = Get-ChildItem -Path "data\cache" -File | Where-Object { 
    $_.Name -match "test|smoke|demo" 
}
if ($testFiles) {
    $testFiles | ForEach-Object {
        Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
    }
    Write-Host "✅ Test cache files removed ($($testFiles.Count) files)" -ForegroundColor Green
} else {
    Write-Host "✅ No test cache files found" -ForegroundColor Green
}

# 5. Report backup folders (user decision)
Write-Host "`n5. Checking backup folders..." -ForegroundColor Yellow
$backups = Get-ChildItem -Path "data\projects" -Directory -Recurse -Filter "backup*" -ErrorAction SilentlyContinue
if ($backups) {
    Write-Host "⚠️  Found $($backups.Count) backup folder(s):" -ForegroundColor Magenta
    $backups | ForEach-Object {
        Write-Host "    $($_.FullName)" -ForegroundColor Gray
    }
    Write-Host "💡 Review manually - may contain user data" -ForegroundColor Yellow
} else {
    Write-Host "✅ No backup folders found" -ForegroundColor Green
}

# 6. Summary
Write-Host "`n📊 Cleanup Summary" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan

$pycacheCount = (Get-ChildItem -Path "." -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue | Measure-Object).Count
$pycCount = (Get-ChildItem -Path "." -Recurse -Filter "*.pyc" -ErrorAction SilentlyContinue | Measure-Object).Count

Write-Host "✅ __pycache__ folders: $pycacheCount" -ForegroundColor Green
Write-Host "✅ .pyc files: $pycCount" -ForegroundColor Green
Write-Host "✅ build/ artifacts: cleaned" -ForegroundColor Green

Write-Host "`n✨ Cleanup complete!" -ForegroundColor Cyan
