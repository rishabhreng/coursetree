import { useEffect, useState } from 'react'
import axios from 'axios'

function App() {
  const [data, setData] = useState('')

  useEffect(() => {
    axios.get('http://localhost:8000/api/data')
      .then(res => setData(res.data.message))
      .catch(err => console.error(err))
  }, [])

  return (
    <div>
      <h1>FastAPI + React</h1>
      <p>Backend says: {data}</p>
    </div>
  )
}
export default App