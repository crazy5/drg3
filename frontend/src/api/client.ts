import axios from 'axios'

const client = axios.create({
  baseURL: '/api',
  timeout: 60000,
})

client.interceptors.response.use(
  (r) => r.data,
  (err) => {
    console.error('[api]', err.response?.data || err.message)
    return Promise.reject(err)
  },
)

export default client
