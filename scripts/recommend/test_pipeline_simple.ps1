Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Recommend System E2E Test" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

Write-Host "`n--- Step 1: Python AI Health Check ---" -ForegroundColor Yellow
try {
    $r1 = Invoke-RestMethod -Uri 'http://127.0.0.1:5100/api/recommend/health' -TimeoutSec 10
    Write-Host "  [OK] Online, Models: $($r1.data.models -join ', ')" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] $_" -ForegroundColor Red
}

Write-Host "`n--- Step 2: Node.js Backend Health Check ---" -ForegroundColor Yellow
try {
    $r2 = Invoke-RestMethod -Uri 'http://127.0.0.1:3000/api/recommend/ai/health' -TimeoutSec 10
    Write-Host "  [OK] $($r2.message)" -ForegroundColor Green
} catch {
    Write-Host "  [FAIL] $_" -ForegroundColor Red
}

Write-Host "`n--- Step 3: Hybrid Recommend (userId=28, topN=5) ---" -ForegroundColor Yellow
try {
    $url3 = 'http://127.0.0.1:3000/api/recommend/ai?userId=28&algorithm=hybrid&topN=5'
    $r3 = Invoke-RestMethod -Uri $url3 -TimeoutSec 120
    Write-Host "  [OK] Algorithm: $($r3.data.algorithm), Elapsed: $($r3.data.elapsed)s, Results: $($r3.data.total)" -ForegroundColor Green
    $r3.data.recommendations | ForEach-Object { Write-Host "    -> Movie #$($_.movieId) | Rating: $($_.predictedRating)" -ForegroundColor DarkYellow }
} catch {
    Write-Host "  [FAIL] $_" -ForegroundColor Red
}

Write-Host "`n--- Step 4: SVD Recommend (userId=28, topN=3) ---" -ForegroundColor Yellow
try {
    $url4 = 'http://127.0.0.1:3000/api/recommend/ai?userId=28&algorithm=svd&topN=3'
    $r4 = Invoke-RestMethod -Uri $url4 -TimeoutSec 120
    Write-Host "  [OK] Elapsed: $($r4.data.elapsed)s, Results: $($r4.data.total)" -ForegroundColor Green
    $r4.data.recommendations | ForEach-Object { Write-Host "    -> Movie #$($_.movieId) | Rating: $($_.predictedRating)" -ForegroundColor DarkYellow }
} catch {
    Write-Host "  [FAIL] $_" -ForegroundColor Red
}

Write-Host "`n--- Step 5: User-CF Recommend (userId=28, topN=3) ---" -ForegroundColor Yellow
try {
    $url5 = 'http://127.0.0.1:3000/api/recommend/ai?userId=28&algorithm=user_cf&topN=3'
    $r5 = Invoke-RestMethod -Uri $url5 -TimeoutSec 120
    Write-Host "  [OK] Elapsed: $($r5.data.elapsed)s, Results: $($r5.data.total)" -ForegroundColor Green
    $r5.data.recommendations | ForEach-Object { Write-Host "    -> Movie #$($_.movieId) | Rating: $($_.predictedRating)" -ForegroundColor DarkYellow }
} catch {
    Write-Host "  [FAIL] $_" -ForegroundColor Red
}

Write-Host "`n--- Step 6: Item-CF Recommend (userId=28, topN=3) ---" -ForegroundColor Yellow
try {
    $url6 = 'http://127.0.0.1:3000/api/recommend/ai?userId=28&algorithm=item_cf&topN=3'
    $r6 = Invoke-RestMethod -Uri $url6 -TimeoutSec 120
    Write-Host "  [OK] Elapsed: $($r6.data.elapsed)s, Results: $($r6.data.total)" -ForegroundColor Green
    $r6.data.recommendations | ForEach-Object { Write-Host "    -> Movie #$($_.movieId) | Rating: $($_.predictedRating)" -ForegroundColor DarkYellow }
} catch {
    Write-Host "  [FAIL] $_" -ForegroundColor Red
}

Write-Host "`n--- Step 7: Different User (userId=188) ---" -ForegroundColor Yellow
try {
    $url7 = 'http://127.0.0.1:3000/api/recommend/ai?userId=188&algorithm=hybrid&topN=5'
    $r7 = Invoke-RestMethod -Uri $url7 -TimeoutSec 120
    Write-Host "  [OK] Elapsed: $($r7.data.elapsed)s, Results: $($r7.data.total)" -ForegroundColor Green
    $r7.data.recommendations | ForEach-Object { Write-Host "    -> Movie #$($_.movieId) | Rating: $($_.predictedRating)" -ForegroundColor DarkYellow }
} catch {
    Write-Host "  [FAIL] $_" -ForegroundColor Red
}

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  All Tests Completed" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan