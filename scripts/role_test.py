import http.client, json

HOST = "127.0.0.1"
PORT = 5000

def req(method, path, body=None, cookie=None):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=20)
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    sc = resp.getheader("Set-Cookie")
    conn.close()
    return resp.status, data, sc

def cookie(sc):
    return sc.split(";")[0] if sc else None

print("=== 1. HEALTH model status (no auth, should be public) ===")
s, body, _ = req("GET", "/api/health")
print("  ", s, body)
print("\n=== 2. MODEL-STATUS (no auth) ===")
s, body, _ = req("GET", "/api/model-status")
print("  ", s, body)

print("\n=== 3. Signup admin (first user -> admin) ===")
s, body, sc = req("POST", "/api/auth/signup", {"username": "admin", "password": "admin123"})
print("  ", s, body)
admin_cookie = cookie(sc)
if admin_cookie is None:
    s, body, sc = req("POST", "/api/auth/login", {"username": "admin", "password": "admin123"})
    print("  (fallback login)", s, body)
    admin_cookie = cookie(sc)

# Ensure stan starts as standard for the negative tests
s, body, _ = req("PUT", "/api/users/stan/role", {"role": "standard"}, cookie=admin_cookie)
print("=== 3b. Reset stan to standard ===")
print("  ", s, body)

print("\n=== 4. /me for admin ===")
s, body, _ = req("GET", "/api/auth/me", cookie=admin_cookie)
print("  ", s, body)

print("\n=== 5. Config PUT as admin ===")
s, body, _ = req("PUT", "/api/config", {"confidence_threshold": 0.25}, cookie=admin_cookie)
print("  ", s, body[:200])

print("\n=== 5b. Config GET as admin ===")
s, body, _ = req("GET", "/api/config", cookie=admin_cookie)
print("  ", s, body[:200])

print("\n=== 6. Users list as admin ===")
s, body, _ = req("GET", "/api/users", cookie=admin_cookie)
print("  ", s, body[:400])

print("\n=== 7. Signup standard user ===")
s, body, sc = req("POST", "/api/auth/signup", {"username": "stan", "password": "stan123"})
print("  ", s, body)
stan_cookie = cookie(sc)
if stan_cookie is None:
    s, body, sc = req("POST", "/api/auth/login", {"username": "stan", "password": "stan123"})
    print("  (fallback login)", s, body)
    stan_cookie = cookie(sc)

print("\n=== 8. Upload as standard user (should 403) ===")
s, body, _ = req("POST", "/api/upload", cookie=stan_cookie)
print("  ", s, body)

print("\n=== 9. Config PUT as standard (should 403) ===")
s, body, _ = req("PUT", "/api/config", {"confidence_threshold": 0.5}, cookie=stan_cookie)
print("  ", s, body)

print("\n=== 10. Users list as standard (should 403) ===")
s, body, _ = req("GET", "/api/users", cookie=stan_cookie)
print("  ", s, body)

print("\n=== 11. Login wrong password (should 401) ===")
s, body, _ = req("POST", "/api/auth/login", {"username": "admin", "password": "wrong"})
print("  ", s, body)

print("\n=== 12. Access protected endpoint without auth (should 401) ===")
s, body, _ = req("GET", "/api/sessions")
print("  ", s, body[:200])

print("\n=== 13. Admin sets stan to manager ===")
s, body, _ = req("PUT", "/api/users/stan/role", {"role": "manager"}, cookie=admin_cookie)
print("  ", s, body)

print("\n=== 14. Standard tries to set role (should 403) ===")
s, body, _ = req("PUT", "/api/users/admin/role", {"role": "standard"}, cookie=stan_cookie)
print("  ", s, body)

print("\n=== 15. Login stan again (should now be manager) ===")
s, body, sc = req("POST", "/api/auth/login", {"username": "stan", "password": "stan123"})
print("  ", s, body)
stan2 = cookie(sc)

print("\n=== 16. Upload as manager now (should pass) ===")
s, body, _ = req("POST", "/api/upload", cookie=stan2)
print("  ", s, body[:200])

print("\n=== 17. Logout ===")
s, body, _ = req("POST", "/api/auth/logout", cookie=admin_cookie)
print("  ", s, body)