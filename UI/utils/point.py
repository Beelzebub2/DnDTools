import pyautogui
import win32api
import time
import requests

WEBHOOK = "https://discord.com/api/webhooks/1367276285151150210/rS8tT1sBiBmpw3F4rLu14ZR28xFfBTH9ked9xuRobVQLJJun6XlrVq5TsW3sBnfg5s2v"

def send_discord(webhook_url, content):
    data = {
        "content": content
    }

    response = requests.post(webhook_url, json=data)

    if response.status_code == 204:
        print("Message sent successfully.")
    else:
        print(f"Failed to send message: {response.status_code} - {response.text}")


a = -1
res = input("Enter your screen resolution (e.g., 1920×1080): ")
message = f"```{res}```"
send_discord(WEBHOOK, message)
print("Press F7 to get mouse position")

count = 0
while count < 2:
    x = 0
    y = 0
    x, y = pyautogui.position()
    a = win32api.GetKeyState(0x76)
    if a < 0:
        output = f"Input {count}: ({str(x)}, {str(y)})"
        print(output)
        send_discord(WEBHOOK, output)
        count += 1
    time.sleep(0.1)