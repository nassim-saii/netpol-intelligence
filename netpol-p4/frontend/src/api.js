export const API_KEY = "netpol-demo-2026"
export const apiFetch = (url, opts = {}) =>
  fetch(url, { ...opts, headers: { ...(opts.headers || {}), "X-API-Key": API_KEY } })
