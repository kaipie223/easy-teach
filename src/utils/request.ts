import axios, { type AxiosInstance, type AxiosResponse, type InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'

/** 创建 axios 实例 */
const request: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 10000,
  headers: {
    'Content-Type': 'application/json'
  }
})

/** 请求拦截器 —— 目前暂无 token，保留结构供后续扩展 */
request.interceptors.request.use(
  (config: InternalAxiosRequestConfig) => {
    // TODO: 后续可从 store 读取 token 并在 headers 中注入
    // const token = useUserStore().token
    // if (token) config.headers.Authorization = `Bearer ${token}`
    return config
  },
  (error) => {
    return Promise.reject(error)
  }
)

/** 响应拦截器 —— 统一提取 data + 错误处理 */
request.interceptors.response.use(
  (response: AxiosResponse) => {
    // 后端统一返回 { code, data, message } 格式时直接解包 data
    // 若后端直接返回数据对象（无 code 包裹），也可直接 return response.data
    const res = response.data
    return res
  },
  (error) => {
    let message = '网络异常，请稍后重试'

    if (error.response) {
      const status = error.response.status
      switch (status) {
        case 400:
          message = error.response.data?.message || '请求参数有误'
          break
        case 401:
          message = '未登录或登录已过期'
          break
        case 403:
          message = '没有权限访问该资源'
          break
        case 404:
          message = '请求的资源不存在'
          break
        case 500:
          message = '服务器内部错误'
          break
        default:
          message = error.response.data?.message || `请求失败 (${status})`
      }
    } else if (error.code === 'ECONNABORTED') {
      message = '请求超时，请检查网络连接'
    }

    ElMessage.error(message)
    return Promise.reject(error)
  }
)

export default request
