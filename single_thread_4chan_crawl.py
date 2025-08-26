import os
import re
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import requests
from lxml import etree
from retrying import retry


class SingleThreadDownloader4chan:
    COUNTER = 0
    HEADER = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/69.0.3497.92 Safari/537.36",
        "cookie": "__cfduid=d903e3abeaca2effe91e7b839a96be7211527491373; _ga=GA1.3.1213173136.1527491373; _ga=GA1.2.2716196.1533521826; _gid=GA1.2.2067292582.1537358233; _gid=GA1.3.2067292582.1537358233; Hm_lvt_ba7c84ce230944c13900faeba642b2b4=1537359428,1537361149,1537362700,1537363469; Hm_lpvt_ba7c84ce230944c13900faeba642b2b4=1537363858",
    }
    PROXIES = {
        "http": "socks5://127.0.0.1:10808",
        "https": "socks5://127.0.0.1:10808",
    }

    def __init__(
        self,
        thread_url: str,
        target_formats: Optional[str] = None,
        download_folder: Optional[str] = None,
    ) -> None:
        SingleThreadDownloader4chan.COUNTER += 1
        self.thread_url = thread_url
        # 从URL中提取board名称
        self.board = self._extract_board_from_url(thread_url)
        self.target_formats = target_formats.split(",") if target_formats else target_formats
        self.download_folder = download_folder if download_folder else "./4chan_thread_download_folder"
        if not os.path.exists(self.download_folder):
            os.makedirs(self.download_folder)
        self.lock = threading.Lock()
        self.pre_download_list = []

    def _extract_board_from_url(self, url: str) -> str:
        """从thread URL中提取board名称"""
        # URL格式类似: https://boards.4chan.org/board/thread/thread_id
        match = re.search(r"https?://boards\.4chan\.org/([^/]+)/thread/", url)
        if match:
            return match.group(1)
        else:
            raise ValueError("无法从URL中提取board名称")

    def request_get_with_retry(self, url, **args):
        default_args = {
            "headers": SingleThreadDownloader4chan.HEADER,
            "proxies": SingleThreadDownloader4chan.PROXIES,
        }
        default_args.update(args)
        # 过滤掉值为None的参数
        default_args = {k: v for k, v in default_args.items() if v is not None}
        return requests.get(url, **default_args)

    def parse_thread_get_img_url(self):
        """解析单个thread获取图片URL"""
        try:
            r = self.request_get_with_retry(self.thread_url)
            thread_name = (
                re.findall(r"<title>(.*?)</title>", r.text)[0]
                .replace("|", "")
                .replace("?", "")
                .replace("*", "")
                .replace("#", "")
                .replace("\\", "")
                .replace("<", "")
                .replace(">", "")
                .replace(":", "")
                .replace("“", "")
                .replace("/", "")
                .split("-")[1]
                .strip()
            )
            print(f"解析帖子: {thread_name}")
            thread_name = f"{SingleThreadDownloader4chan.COUNTER:02d}.{thread_name}"

            html = etree.HTML(r.content)
            imgs = html.xpath(".//a[@class='fileThumb']/@href")
            imgs = ["https:" + i for i in imgs]
            f_name = html.xpath(".//div[@class='fileText']/a/text()")
            imgs_f_name = list(zip(imgs, f_name))

            # 过滤目标格式
            if self.target_formats:
                filtered_imgs_f_name = []
                for img_f_name in imgs_f_name:
                    if img_f_name[0].lower().split(".")[-1] in self.target_formats:
                        filtered_imgs_f_name.append(img_f_name)
                imgs_f_name = filtered_imgs_f_name

            # 构建下载列表，并为文件名添加序号
            for idx, img_f_name in enumerate(imgs_f_name, start=1):
                # 直接在原始文件名前添加序号前缀，格式为001.original_filename
                numbered_filename = f"{idx:03d}.{img_f_name[1]}"
                self.pre_download_list.append([thread_name, img_f_name[0], numbered_filename])

        except Exception as e:
            print(f"解析帖子时出错: {e}")
            print(traceback.format_exc())

    def downloader(self, items):
        """下载单个图片"""
        try:
            thread_name, img, f_name = items
            download_folder = os.path.join(self.download_folder, self.board, thread_name)
            with self.lock:
                if not os.path.exists(download_folder):
                    os.makedirs(download_folder)
            content = self.request_get_with_retry(img).content
            with open(os.path.join(download_folder, f_name), "wb") as f:
                f.write(content)
            print(f"已下载: {f_name}")
        except Exception as e:
            print(f"下载图片时出错: {e}")
            print(traceback.format_exc())

    def run(self):
        """执行下载流程"""
        try:
            # 解析thread获取图片信息
            self.parse_thread_get_img_url()

            # 如果没有需要下载的图片，直接返回
            if not self.pre_download_list:
                print("没有找到需要下载的图片")
                return

            # 使用线程池下载图片
            print(f"开始下载 {len(self.pre_download_list)} 张图片...")
            with ThreadPoolExecutor(8) as executor:
                executor.map(self.downloader, self.pre_download_list)

            print("下载完成...")
        except Exception as e:
            print(f"执行过程中出错: {e}")
            print(traceback.format_exc())


if __name__ == "__main__":
    # 从命令行参数获取可选的下载文件夹
    if len(sys.argv) == 2:
        download_folder = sys.argv[1]
    else:
        download_folder = None

    exe = ThreadPoolExecutor(1)

    while True:
        thread_url = input("请输入4chan的thread URL: ").strip()
        if not thread_url:
            continue
        exe.submit(lambda thread_url: SingleThreadDownloader4chan(thread_url, download_folder=download_folder).run(), thread_url)
