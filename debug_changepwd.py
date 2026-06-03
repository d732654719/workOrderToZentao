"""提交改密表单，清除 session 重定向"""
import json, requests, re, sys, hashlib
sys.stdout.reconfigure(encoding='utf-8')

from config_loader import load_config, ensure_credentials, ZENTAO_URL
_zcfg = ensure_credentials(load_config()).get("zentao", {})
PWD = _zcfg.get("password", "")
ACCOUNT = _zcfg.get("account", "dengchang")

s = requests.Session()

PWD_MD5 = hashlib.md5(PWD.encode()).hexdigest()
PWD_LEN = str(len(PWD))

# 登录 (登录接口接受明文)
r = s.get(f'{ZENTAO_URL}/api-getsessionid.json')
sid = json.loads(json.loads(r.text)['data'])['sessionID']
s.post(f'{ZENTAO_URL}/user-login.json?zentaosid={sid}',
       data={'account': ACCOUNT, 'password': PWD})
print('Login OK')

# 获取改密页面，提取 verifyRand
r = s.get(f'{ZENTAO_URL}/my-changepassword.html?zentaosid={sid}')
match = re.search(r'"verifyRand"[^>]+value="(\d+)"', r.text)
verify_rand = match.group(1) if match else None
print(f'verifyRand: {verify_rand}')

# 查看页面JS如何处理密码
md5_js = re.findall(r'(password|md5|MD5|hash)[^;]+', r.text)
print(f'Password-related JS: {md5_js[:3]}')

# 禅道改密表单: password1 = MD5(password) + passwordLength
pwd_hashed = PWD_MD5 + PWD_LEN
print(f'MD5({PWD}) = {PWD_MD5}')
print(f'Hashed pwd = {pwd_hashed}')

# 提交改密表单 — 使用MD5加密的密码
r = s.post(f'{ZENTAO_URL}/my-changePassword.html?zentaosid={sid}',
    data={
        'account': ACCOUNT,
        'originalPassword': PWD_MD5,  # MD5 of current password
        'password1': pwd_hashed,      # MD5(password) + length
        'password2': pwd_hashed,
        'passwordLength': PWD_LEN,
        'verifyRand': verify_rand,
    },
    headers={'Referer': f'{ZENTAO_URL}/my-changepassword.html?zentaosid={sid}'}
)
print(f'Submit: status={r.status_code}, response={r.text[:400]}')

# 测试各页面
for label, url in [
    ('Product', f'{ZENTAO_URL}/product-index-no.json?zentaosid={sid}'),
    ('Project', f'{ZENTAO_URL}/project-index.json?zentaosid={sid}'),
    ('My-index', f'{ZENTAO_URL}/my-index.json?zentaosid={sid}'),
    ('Execution', f'{ZENTAO_URL}/execution-all.json?zentaosid={sid}'),
]:
    r = s.get(url)
    data = json.loads(json.loads(r.text)['data'])
    title = data.get('title', '?')
    extra = {k: type(v).__name__ for k, v in data.items() if k not in ['title','isonlybody','user','rand','pager']}
    print(f'{label}: title={title}, extra={extra}')
