@echo off
echo ============================================================
echo PropSense.AI — Test and Verify Sequence
echo Run from the propsense/ directory
echo ============================================================

echo.
echo [1/5] Re-upserting ROI chunks (adds psf_cagr_pct to Pinecone metadata)
echo       CRITICAL: integration test 4 will fail without this step.
python -m rag.reupsert_roi
if errorlevel 1 (
    echo FAILED: reupsert_roi
    goto error
)

echo.
echo [2/5] Narrator smoke test (gpt-4o-mini via personal OpenAI key)...
python -m llm.narrator
if errorlevel 1 (
    echo FAILED: narrator
    goto error
)

echo.
echo [3/5] Integration tests (18 tests, 5 required scenarios)...
pytest tests/integration/test_pipeline.py -v
if errorlevel 1 (
    echo FAILED: integration tests
    goto error
)

echo.
echo [4/5] Frontend setup (run once)...
cd frontend
npm install
cd ..

echo.
echo [5/5] Starting services for manual smoke test:
echo   - In terminal 1: uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
echo   - In terminal 2: cd frontend ^&^& npm run dev
echo   - Then open: http://localhost:3000

echo.
echo ============================================================
echo All automated steps complete!
echo ============================================================
goto end

:error
echo.
echo ERROR in the step above. Fix and re-run.
exit /b 1

:end
