#!/usr/bin/env bash
# Starts the whole demo with one command:
#   U-billing mock (8081) + mapping mock (8082) + BizOS (8000)
#
#   chmod +x run_demo.sh && ./run_demo.sh
set -e
cd "$(dirname "$0")"

echo "--- checking dependencies ---"
python3 -c "import fastapi, uvicorn, sqlalchemy, httpx, yaml" 2>/dev/null \
  || pip3 install -r requirements.txt

if [ ! -f bizos.db ]; then
  echo "--- seeding the database with demo data ---"
  python3 -m app.seeds
fi

echo "--- starting the U-billing mock (port 8081) ---"
python3 -m uvicorn mocks.ubilling_mock:app --port 8081 --log-level warning &
UB=$!
echo "--- starting the mapping mock (port 8082) ---"
python3 -m uvicorn mocks.grusher_mock:app --port 8082 --log-level warning &
GR=$!

cleanup(){ echo; echo "stopping the mocks..."; kill $UB $GR 2>/dev/null || true; }
trap cleanup EXIT

sleep 3
echo
echo "========================================================"
echo "  Console:    http://localhost:8000/panel"
echo "  Swagger:    http://localhost:8000/docs"
echo "  U-billing:  http://localhost:8081/docs"
echo "  Mapping:    http://localhost:8082/docs"
echo "  Stop with:  Ctrl+C"
echo "========================================================"
echo

BILLING_ADAPTER=mock MAPS_ADAPTER=mock SMS_GATEWAY=sandbox \
  python3 -m uvicorn app.main:app --port 8000
