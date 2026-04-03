cd ./api
uvicorn index:app --reload > api.log 2>&1 &

cd ..
npm run dev > client.log 2>&1 &