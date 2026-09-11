from __future__ import annotations

import os

import uvicorn


if __name__ == "__main__":
    # Only explicitly configured proxy peers may supply the public scheme/client IP.
    trusted_proxies = os.getenv("NIUNO3_TRUSTED_PROXIES", "").strip()
    uvicorn.run(
        "app.dashboard.api:create_app",
        factory=True,
        host=os.getenv("NIUNO3_HOST", "127.0.0.1"),
        port=int(os.getenv("NIUNO3_PORT", "8789")),
        proxy_headers=bool(trusted_proxies),
        forwarded_allow_ips=trusted_proxies,
    )
