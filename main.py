import requests
from PIL import Image
from io import BytesIO
from bs4 import BeautifulSoup
from captcha_ocr import get_ocr_res
import os
from dotenv import load_dotenv
import json
from dingtalk import dingtalk
from feishu import feishu
import logging
import re
from datetime import datetime

load_dotenv()

# 核心环境变量（新增账号索引区分）
DD_BOT_TOKEN = os.getenv("DD_BOT_TOKEN")
DD_BOT_SECRET = os.getenv("DD_BOT_SECRET")
FEISHU_BOT_URL = os.getenv("FEISHU_BOT_URL")
FEISHU_BOT_SECRET = os.getenv("FEISHU_BOT_SECRET")
SEMESTER = os.getenv("SEMESTER", "2024-2025-2")
ACCOUNT_INDEX = os.getenv("ACCOUNT_INDEX", "1")  # 账号索引（1或2）

# 日志配置
logging.basicConfig(
    level=logging.INFO, format=f"[账号{ACCOUNT_INDEX}] %(asctime)s - %(levelname)s - %(message)s"
)

# 教务系统URL
RandCodeUrl = "http://zhjw.qfnu.edu.cn/verifycode.servlet"
loginUrl = "http://zhjw.qfnu.edu.cn/Logon.do?method=logonLdap"
dataStrUrl = "http://zhjw.qfnu.edu.cn/Logon.do?method=logon&flag=sess"


def get_initial_session():
    """创建会话并获取初始数据"""
    session = requests.session()
    response = session.get(dataStrUrl, timeout=1000)
    cookies = session.cookies.get_dict()
    return session, cookies, response.text


def handle_captcha(session, cookies):
    """获取并识别验证码"""
    response = session.get(RandCodeUrl, cookies=cookies)
    if response.status_code != 200:
        logging.error(f"请求验证码失败，状态码: {response.status_code}")
        return None

    try:
        image = Image.open(BytesIO(response.content))
    except Exception as e:
        logging.error(f"无法识别图像文件: {e}")
        return None

    return get_ocr_res(image)


def generate_encoded_string(data_str, user_account, user_password):
    """生成登录所需的encoded字符串"""
    res = data_str.split("#")
    code, sxh = res[0], res[1]
    data = f"{user_account}%%%{user_password}"
    encoded = ""
    b = 0

    for a in range(len(code)):
        if a < 20:
            encoded += data[a] if a < len(data) else ""
            for _ in range(int(sxh[a])):
                encoded += code[b] if b < len(code) else ""
                b += 1
        else:
            encoded += data[a:] if a < len(data) else ""
            break
    return encoded


def login(session, cookies, user_account, user_password, random_code, encoded):
    """执行登录操作"""
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Origin": "http://zhjw.qfnu.edu.cn",
        "Referer": "http://zhjw.qfnu.edu.cn/",
    }

    data = {
        "userAccount": user_account,
        "userPassword": user_password,
        "RANDOMCODE": random_code,
        "encoded": encoded,
    }

    return session.post(loginUrl, headers=headers, data=data, cookies=cookies, timeout=1000)


def get_user_credentials():
    """获取当前账号的登录信息（区分账号1/2）"""
    user_account = os.getenv(f"USER_ACCOUNT_{ACCOUNT_INDEX}")
    user_password = os.getenv(f"USER_PASSWORD_{ACCOUNT_INDEX}")
    if user_account and user_password:
        logging.info(f"用户名: {user_account[:2]}{'*' * (len(user_account)-2)}")
        logging.info(f"密码: {'*' * len(user_password)}")
    else:
        logging.error(f"请设置账号{ACCOUNT_INDEX}的USER_ACCOUNT_{ACCOUNT_INDEX}和USER_PASSWORD_{ACCOUNT_INDEX}")
        return None, None
    return user_account, user_password


def simulate_login(user_account, user_password):
    """模拟登录过程"""
    session, cookies, data_str = get_initial_session()

    for attempt in range(3):
        random_code = handle_captcha(session, cookies)
        if not random_code:
            logging.warning(f"验证码识别失败，重试第{attempt+1}次")
            continue

        logging.info(f"验证码: {random_code}")
        encoded = generate_encoded_string(data_str, user_account, user_password)
        response = login(session, cookies, user_account, user_password, random_code, encoded)

        if response.status_code == 200:
            if "验证码错误!!" in response.text:
                logging.warning(f"验证码错误，重试第{attempt+1}次")
                continue
            if "密码错误" in response.text:
                raise Exception("用户名或密码错误")
            logging.info("登录成功")
            return session, cookies
        else:
            raise Exception(f"登录失败，状态码: {response.status_code}")

    raise Exception("验证码多次识别失败，请重试")


def get_score_page(session, cookies):
    """访问成绩页面"""
    url = "http://zhjw.qfnu.edu.cn/jsxsd/kscj/cjcx_list"
    response = session.get(url, cookies=cookies)
    return response.text


def analyze_score_page(pagehtml):
    """解析成绩页面"""
    soup = BeautifulSoup(pagehtml, "lxml")
    results = []
    table = soup.find("table", {"id": "dataList"})
    
    if table:
        rows = table.find_all("tr")[1:]  # 跳过表头
        for row in rows:
            columns = row.find_all("td")
            if len(columns) > 5:
                subject_name = columns[3].get_text(strip=True)
                score = columns[5].get_text(strip=True)
                results.append((subject_name, score))
    return results


def get_new_scores(current_scores, last_scores):
    """获取新增的成绩"""
    return [score for score in current_scores if score not in last_scores]


def print_welcome():
    logging.info("\n" + "*"*10 + f" 账号{ACCOUNT_INDEX} - 曲阜师范大学成绩监控 " + "*"*10)
    logging.info("By W1ndys | https://github.com/W1ndys/QFNUScoreReminder")


def save_scores_to_file(scores):
    """保存当前账号的成绩到带索引的文件"""
    filename = f"scores_{ACCOUNT_INDEX}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(scores, f, ensure_ascii=False, indent=4)


def load_scores_from_file():
    """加载当前账号的历史成绩（带索引）"""
    filename = f"scores_{ACCOUNT_INDEX}.json"
    try:
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                data = f.read().strip()
                return json.loads(data) if data else []
        else:
            logging.info(f"初始化账号{ACCOUNT_INDEX}的成绩文件: {filename}")
            with open(filename, "w", encoding="utf-8") as f:
                f.write("[]")
            return []
    except Exception as e:
        logging.error(f"读取成绩文件失败: {e}")
        return []


def safe_file_write(content, mode="w"):
    """安全写入当前账号的输出文件（带索引）"""
    filename = f"output_{ACCOUNT_INDEX}.txt"
    try:
        os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
        with open(filename, mode, encoding="utf-8") as f:
            f.write(content)
        return True
    except Exception as e:
        logging.error(f"写入文件{filename}失败: {e}")
        return False


def get_all_semester_scores(session, cookies):
    """获取全部学期的绩点和学分"""
    url = "http://zhjw.qfnu.edu.cn/jsxsd/kscj/cjcx_list"
    response = session.get(url, cookies=cookies)
    total_credits_match = re.search(r"所修总学分:(\d+)", response.text)
    average_gpa_match = re.search(r"平均学分绩点:(\d+\.\d+)", response.text)
    return (total_credits_match.group(1) if total_credits_match else None,
            average_gpa_match.group(1) if average_gpa_match else None)


def parse_credits_and_gpa(session, cookies):
    """解析本学期学分和绩点"""
    url = f"http://zhjw.qfnu.edu.cn/jsxsd/kscj/cjcx_list?kksj={SEMESTER}&kcxz=&kcmc=&xsfs=all"
    response = session.get(url, cookies=cookies)
    soup = BeautifulSoup(response.text, "lxml")
    results = []
    table = soup.find("table", {"id": "dataList"})
    
    if table:
        rows = table.find_all("tr")[1:]
        for row in rows:
            columns = row.find_all("td")
            if len(columns) > 9:
                try:
                    credits = float(columns[7].get_text(strip=True))
                    gpa = float(columns[9].get_text(strip=True))
                    results.append((credits, gpa))
                except ValueError:
                    continue
    return results


def calculate_average_gpa(credits_and_points):
    """计算平均学分绩点"""
    total_points = sum(c * g for c, g in credits_and_points)
    total_credits = sum(c for c, g in credits_and_points)
    return total_points / total_credits if total_credits > 0 else 0


def validate_credentials(user_account, user_password):
    """验证当前账号的凭据"""
    if not user_account or not user_password:
        return False
    if ACCOUNT_INDEX not in ["1", "2"]:
        logging.error("ACCOUNT_INDEX必须为1或2")
        return False
    return True


def notify_connection_issue(user_account):
    """通知连接问题"""
    msg = f"账号{ACCOUNT_INDEX}（学号: {user_account}）\n无法连接教务系统，请检查网络或系统可用性"
    logging.error(msg)
    if DD_BOT_TOKEN and DD_BOT_SECRET:
        dingtalk(DD_BOT_TOKEN, DD_BOT_SECRET, "成绩监控通知", msg)


def process_scores(session, cookies, user_account):
    """处理当前账号的成绩"""
    last_scores = load_scores_from_file()
    current_scores = analyze_score_page(get_score_page(session, cookies))
    current_scores_converted = [list(score) for score in current_scores]

    if not last_scores:
        initialize_scores(current_scores_converted, user_account)
    elif current_scores_converted != last_scores:
        logging.info(f"账号{ACCOUNT_INDEX}发现成绩变化")
        update_scores(current_scores_converted, last_scores, user_account)
    else:
        logging.info(f"账号{ACCOUNT_INDEX}无新成绩")


def initialize_scores(scores, user_account):
    """初始化当前账号的成绩"""
    save_scores_to_file(scores)
    msg = f"初始化保存成功！\n当前已记录{len(scores)}门课程成绩"
    notify_new_scores(msg, user_account)


def update_scores(current, last, user_account):
    """更新当前账号的成绩并通知"""
    new_scores = get_new_scores(current, last)
    if new_scores:
        msg = "\n".join([f"科目: {s[0]}\n成绩: {s[1]}" for s in new_scores])
        notify_new_scores(f"发现{len(new_scores)}门新成绩！\n{msg}", user_account)
        save_scores_to_file(current)


def notify_new_scores(message, user_account):
    """发送新成绩通知（带账号标识）"""
    full_msg = f"账号{ACCOUNT_INDEX}（学号: {user_account}）\n{message}"
    logging.info(f"发送通知: {full_msg}")
    
    # 钉钉通知（必选）
    if DD_BOT_TOKEN and DD_BOT_SECRET:
        dingtalk(DD_BOT_TOKEN, DD_BOT_SECRET, "成绩监控通知", full_msg)
    
    # 飞书通知（可选，存在配置才发送）
    if FEISHU_BOT_URL and FEISHU_BOT_SECRET:
        feishu(title="成绩监控通知", content=full_msg)


def handle_exception(e, user_account):
    """处理异常并通知"""
    msg = f"发生错误: {str(e)}"
    logging.error(msg)
    if DD_BOT_TOKEN and DD_BOT_SECRET:
        dingtalk(DD_BOT_TOKEN, DD_BOT_SECRET, "成绩监控错误", 
                f"账号{ACCOUNT_INDEX}（学号: {user_account}）\n{msg}")


def main():
    """主函数"""
    load_dotenv()
    print_welcome()
    try:
        user_account, user_password = get_user_credentials()
        if not validate_credentials(user_account, user_password):
            return

        session, cookies = simulate_login(user_account, user_password)
        if not session or not cookies:
            notify_connection_issue(user_account)
            return

        # 处理成绩
        process_scores(session, cookies, user_account)

        # 计算并保存绩点
        total_credits, avg_gpa = get_all_semester_scores(session, cookies)
        sem_gpa = calculate_average_gpa(parse_credits_and_gpa(session, cookies))
        
        # 保存当前账号的绩点信息
        safe_file_write(
            f"账号{ACCOUNT_INDEX} 总学分: {total_credits or '未知'}\n"
            f"账号{ACCOUNT_INDEX} 平均绩点: {avg_gpa or '未知'}\n"
            f"账号{ACCOUNT_INDEX} {SEMESTER} 学期绩点: {sem_gpa:.2f}\n"
        )

    except Exception as e:
        handle_exception(e, user_account)


if __name__ == "__main__":
    main()
