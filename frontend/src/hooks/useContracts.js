import { useState, useEffect, useCallback } from 'react'
import client from '../api/client'

export function useContracts({ page = 1, perPage = 15, status = '', type = '' } = {}) {
  const [data, setData] = useState([])
  const [total, setTotal] = useState(0)
  const [pages, setPages] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetch = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = new URLSearchParams({ page, per_page: perPage })
      if (status) params.set('status', status)
      if (type) params.set('type', type)
      const res = await client.get(`/contracts?${params}`)
      // Dedup by id
      const seen = new Set()
      const deduped = (res.data.data || []).filter(c => {
        if (seen.has(c.id)) return false
        seen.add(c.id)
        return true
      })
      setData(deduped)
      setTotal(res.data.total || 0)
      setPages(res.data.pages || 1)
    } catch (e) {
      setError(e.response?.data?.error?.message || 'Failed to load contracts')
    } finally {
      setLoading(false)
    }
  }, [page, perPage, status, type])

  useEffect(() => { fetch() }, [fetch])

  return { data, total, pages, loading, error, refetch: fetch }
}

export function useContract(id) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!id) return
    setLoading(true)
    client.get(`/contracts/${id}`)
      .then(res => setData(res.data))
      .catch(e => setError(e.response?.data?.error?.message || 'Failed to load'))
      .finally(() => setLoading(false))
  }, [id])

  return { data, loading, error }
}
