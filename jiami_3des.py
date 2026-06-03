# import json
#
# from Crypto.Cipher import AES,DES3
# from base64 import b64decode, b64encode
#
# BLOCK_SIZE = DES3.block_size
# # 不足BLOCK_SIZE的补位(s可能是含中文，而中文字符utf-8编码占3个位置,gbk是2，所以需要以len(s.encode())，而不是len(s)计算补码)
# pad = lambda s: s + (BLOCK_SIZE - len(s.encode()) % BLOCK_SIZE) * chr(BLOCK_SIZE - len(s.encode()) % BLOCK_SIZE)
# # 去除补位
# unpad = lambda s: s[:-ord(s[len(s) - 1:])]
#
#
# class DES3Cipher:
#     def __init__(self, secretkey: str,iv:str):
#         self.key = secretkey  # 密钥
#         self.iv = iv  # 偏移量
#
#     def encrypt(self, text):
#         """
#         加密 ：先补位，再AES加密，后base64编码
#         :param text: 需加密的明文
#         :return:
#         """
#         # text = pad(text) 包pycrypto的写法，加密函数可以接受str也可以接受bytess
#         text = pad(text).encode()  # 包pycryptodome 的加密函数不接受str
#         cipher = DES3.new(key=self.key.encode(), mode=AES.MODE_CBC, IV=self.iv.encode())
#         encrypted_text = cipher.encrypt(text)
#         # 进行64位的编码,返回得到加密后的bytes，decode成字符串
#         return b64encode(encrypted_text).decode('utf-8')
#
#     def decrypt(self, encrypted_text):
#         """
#         解密 ：偏移量为key[0:16]；先base64解，再AES解密，后取消补位
#         :param encrypted_text : 已经加密的密文
#         :return:
#         """
#         encrypted_text = b64decode(encrypted_text)
#         cipher = AES.new(key=self.key.encode(), mode=AES.MODE_CBC, IV=self.iv.encode())
#         decrypted_text = cipher.decrypt(encrypted_text)
#         return unpad(decrypted_text).decode('utf-8')
#
#
# if __name__=="__main__":
#     # data='{"parkId": "592011694","sellerName": "商家C","sellerId": "d7ff8d4021db45be8a25b49da9dd374d"}'
#     # res=DES3Cipher('Keytop:1234567812345678!','20210723').encrypt(data)
#     # print(res)
#
#
#     res=DES3Cipher('Keytop:1234567812345678!','20220725').decrypt("DES3Cipher('Keytop:1234567812345678!','20220725').")
#     print(res)
#     # secretkey = 'Keytop:1234567812345678!'
#     # text = '使用 pycryptodome 进行 AES/CBC/PKCS5(算法/模式/补码方式) 加密'  # 待加密的明文
#     # encrypted_text = AESCipher(secretkey).encrypt(text)  # 加密
#     # print(encrypted_text)
import json
from Crypto.Cipher import DES3
from base64 import b64decode, b64encode

BLOCK_SIZE = DES3.block_size  # 3DES的分组大小为8字节

# 补位和去补位函数（基于3DES的8字节分组）
pad = lambda s: s + (BLOCK_SIZE - len(s.encode()) % BLOCK_SIZE) * chr(BLOCK_SIZE - len(s.encode()) % BLOCK_SIZE)
unpad = lambda s: s[:-ord(s[len(s) - 1:])]


class DES3Cipher:
    def __init__(self, secretkey: str, iv: str):
        # 3DES密钥必须是16字节（2密钥）或24字节（3密钥），iv为8字节
        self.key = secretkey.encode()
        self.iv = iv.encode()  # iv长度必须为8字节（3DES要求）

    def encrypt(self, text):
        text = pad(text).encode()
        # 使用DES3算法，CBC模式
        cipher = DES3.new(self.key, DES3.MODE_CBC, self.iv)
        encrypted_text = cipher.encrypt(text)
        return b64encode(encrypted_text).decode('utf-8')

    def decrypt(self, encrypted_text):
        encrypted_text = b64decode(encrypted_text)
        cipher = DES3.new(self.key, DES3.MODE_CBC, self.iv)
        decrypted_text = cipher.decrypt(encrypted_text)
        return unpad(decrypted_text).decode('utf-8')
if __name__=="__main__":
    res = DES3Cipher('Keytop:1234567812345678!','20220725').decrypt("RupVqDzlIig/6R+E7nVZ9pOxsAtTOlJNOMqtWFbFcY8/039dHn6lqs61g+erirWIM5Fpd6RqZXcgEy1Jx+FHyh0ldPFpiaK5KbH8bAHkXebVrLCCnFrajZexIRJGF9HE+VIEtk6WRGvRoIwNKZsYug==")
    print(res)