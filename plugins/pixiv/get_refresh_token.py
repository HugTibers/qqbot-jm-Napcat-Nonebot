"""
获取 Pixiv refresh_token - 快速版

⚠️ 重要提示：code 只有约 30 秒有效期，必须快速操作！
"""

import secrets
import hashlib
import base64
import requests
from urllib.parse import urlencode

# Pixiv OAuth 配置
CLIENT_ID = "MOBrBDS8blbauoSck0ZfDbtuzpyT"
CLIENT_SECRET = "lsACyCD94FhDUtGTXi3QzcFE2uU1hqtDaKeqrdwj"
REDIRECT_URI = "https://app-api.pixiv.net/web/v1/users/auth/pixiv/callback"
LOGIN_URL = "https://app-api.pixiv.net/web/v1/login"
AUTH_TOKEN_URL = "https://oauth.secure.pixiv.net/auth/token"


def generate_login_url():
    """生成登录 URL 和 code_verifier"""
    code_verifier = secrets.token_urlsafe(32)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b'=').decode()
    
    params = {
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "client": "pixiv-android",
    }
    
    login_url = f"{LOGIN_URL}?{urlencode(params)}"
    return login_url, code_verifier


def exchange_code_for_token(code: str, code_verifier: str):
    """用授权码换取 token"""
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "code_verifier": code_verifier,
        "grant_type": "authorization_code",
        "include_policy": "true",
        "redirect_uri": REDIRECT_URI,
    }
    
    response = requests.post(
        AUTH_TOKEN_URL,
        data=data,
        headers={"User-Agent": "PixivAndroidApp/5.0.234"},
    )
    
    return response.json()


if __name__ == "__main__":
    print("=" * 60)
    print("Pixiv refresh_token 获取工具")
    print("=" * 60)
    
    # 生成登录 URL
    login_url, code_verifier = generate_login_url()
    
    print("\n📋 操作步骤（必须快速完成，code 30 秒内过期！）：")
    print("\n【步骤 1】复制下面的 URL 到浏览器打开并登录：")
    print("-" * 60)
    print(login_url)
    print("-" * 60)
    
    print("\n【步骤 2】登录成功后，在开发者工具(F12)->网络 中")
    print("         找到 callback 请求，复制 URL 中的 code= 后面的值")
    print("         或者看到 pixiv://account/login?code=XXXXX 的链接")
    
    print("\n【步骤 3】快速粘贴 code（30秒内！）")
    print("-" * 60)
    
    code = input("请粘贴 code: ").strip()
    
    if not code:
        print("❌ 没有输入 code")
        exit(1)
    
    print("\n正在获取 token...")
    result = exchange_code_for_token(code, code_verifier)
    
    if "refresh_token" in result:
        print("\n" + "=" * 60)
        print("✅ 成功！")
        print("=" * 60)
        print(f"\naccess_token:  {result['access_token'][:40]}...")
        print(f"\nrefresh_token: {result['refresh_token']}")
        print("\n👆 请复制上面的 refresh_token 保存到你的代码中！")
        print("=" * 60)
        
        # 保存到文件
        with open("my_refresh_token.txt", "w") as f:
            f.write(result['refresh_token'])
        print("\n💾 已保存到 my_refresh_token.txt")
    else:
        print(f"\n❌ 失败: {result}")
        if "expired" in str(result):
            print("\n💡 提示：code 过期了，请重新运行脚本并快速操作！")
