Write-Host "🔥 RESETTING SHOPMAX DATABASE..." -ForegroundColor Yellow

# Stop any running Flask app (you'll need to do this manually with Ctrl+C)

# Delete database
if (Test-Path "shopmax.db") {
    Remove-Item shopmax.db
    Write-Host "✅ Deleted shopmax.db" -ForegroundColor Green
} else {
    Write-Host "⚠️ shopmax.db not found" -ForegroundColor Yellow
}

# Delete migrations folder
if (Test-Path "migrations") {
    Remove-Item -Recurse -Force migrations
    Write-Host "✅ Deleted migrations folder" -ForegroundColor Green
} else {
    Write-Host "⚠️ migrations folder not found" -ForegroundColor Yellow
}

# Delete all __pycache__ folders
Get-ChildItem -Path . -Filter "__pycache__" -Recurse | ForEach-Object {
    Remove-Item -Recurse -Force $_.FullName
    Write-Host "✅ Deleted $($_.FullName)" -ForegroundColor Green
}

Write-Host ""
Write-Host "✅ RESET COMPLETE!" -ForegroundColor Green
Write-Host ""
Write-Host "Now run these commands:" -ForegroundColor Cyan
Write-Host "flask db init" -ForegroundColor White
Write-Host "flask db migrate -m 'initial schema'" -ForegroundColor White
Write-Host "flask db upgrade" -ForegroundColor White
Write-Host "python app.py" -ForegroundColor White