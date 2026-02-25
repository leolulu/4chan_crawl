import argparse
import json
import os
import re
import time
import traceback
from typing import Optional

import lxml.etree as etree
import requests


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
        title_keywords: Optional[list[str]] = None,
    ) -> None:
        SingleThreadDownloader4chan.COUNTER += 1
        self.thread_url = thread_url
        # 从URL中提取board名称
        self.board = self._extract_board_from_url(thread_url)
        self.target_formats = (
            target_formats.split(",") if target_formats else target_formats
        )
        self.title_keywords = (
            [keyword.casefold() for keyword in title_keywords]
            if title_keywords
            else None
        )
        self.download_folder = (
            download_folder if download_folder else "./4chan_thread_download_folder"
        )
        if not os.path.exists(self.download_folder):
            os.makedirs(self.download_folder)
        self.pre_download_list = []

    def _extract_board_from_url(self, url: str) -> str:
        """从thread URL中提取board名称"""
        # URL格式类似: https://boards.4chan.org/board/thread/thread_id
        match = re.search(r"https?://boards\.4chan\.org/([^/]+)/thread/", url)
        if match:
            return match.group(1)
        else:
            raise ValueError("无法从URL中提取board名称")

    @staticmethod
    def request_get_with_retry(
        url: str,
        headers: Optional[dict[str, str]] = None,
        proxies: Optional[dict[str, str]] = None,
    ):
        final_headers = (
            SingleThreadDownloader4chan.HEADER if headers is None else headers
        )
        final_proxies = (
            SingleThreadDownloader4chan.PROXIES if proxies is None else proxies
        )
        attempts = 10
        for attempt in range(1, attempts + 1):
            try:
                response = requests.get(
                    url, headers=final_headers, proxies=final_proxies
                )
                response.raise_for_status()
                return response
            except requests.RequestException:
                if attempt == attempts:
                    raise
                time.sleep(2)
        raise RuntimeError("请求重试次数耗尽")

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
            if self.title_keywords and not any(
                keyword in thread_name.casefold() for keyword in self.title_keywords
            ):
                print(f"跳过帖子(标题不匹配): {thread_name}")
                return
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
                self.pre_download_list.append(
                    [thread_name, img_f_name[0], numbered_filename]
                )

        except Exception as e:
            print(f"解析帖子时出错: {e}")
            print(traceback.format_exc())

    def downloader(self, items):
        """下载单个图片"""
        try:
            thread_name, img, f_name = items
            download_folder = os.path.join(
                self.download_folder, self.board, thread_name
            )
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
            for item in self.pre_download_list:
                self.downloader(item)

            print("下载完成...")
        except Exception as e:
            print(f"执行过程中出错: {e}")
            print(traceback.format_exc())


def fetch_catalog_threads(catalog_url: str):
    response = SingleThreadDownloader4chan.request_get_with_retry(
        catalog_url,
        headers=SingleThreadDownloader4chan.HEADER,
        proxies=SingleThreadDownloader4chan.PROXIES,
    )
    catalog_match = re.search(
        r"var catalog = (\{.*?\});var style_group", response.text, re.S
    )
    if not catalog_match:
        raise ValueError("无法从catalog页面提取thread数据")
    catalog_data = json.loads(catalog_match.group(1))
    slug = catalog_data.get("slug", "gif")
    threads_data = catalog_data.get("threads", {})
    threads = []
    for thread_id, thread_info in threads_data.items():
        thread_title = (
            thread_info.get("sub") or thread_info.get("teaser") or ""
        ).strip()
        thread_url = f"https://boards.4chan.org/{slug}/thread/{thread_id}"
        threads.append((thread_title, thread_url))
    return threads


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="4chan 单帖图片下载器",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "download_folder",
        nargs="?",
        default=None,
        help="可选下载目录，默认使用 ./4chan_thread_download_folder",
    )
    parser.add_argument(
        "--title-keyword",
        action="append",
        required=True,
        metavar="KEYWORD",
        help=(
            "按标题关键字筛选(不区分大小写)，可重复传入多个\n"
            "匹配规则: 命中任意一个关键字(OR)即下载\n"
            "示例: --title-keyword milf --title-keyword bbw\n"
            "输入清洗: 自动去首尾空格、去重、忽略空值"
        ),
    )
    args = parser.parse_args()
    download_folder = args.download_folder
    title_keywords = []
    seen_keywords = set()
    for keyword in args.title_keyword:
        cleaned_keyword = keyword.strip()
        if not cleaned_keyword:
            continue
        folded_keyword = cleaned_keyword.casefold()
        if folded_keyword in seen_keywords:
            continue
        seen_keywords.add(folded_keyword)
        title_keywords.append(cleaned_keyword)
    if not title_keywords:
        raise SystemExit("错误: 至少需要一个非空的 --title-keyword")
    catalog_url = "https://boards.4chan.org/gif/catalog"
    all_threads = fetch_catalog_threads(catalog_url)
    matched_threads = []
    for thread_title, thread_url in all_threads:
        if any(
            keyword.casefold() in thread_title.casefold() for keyword in title_keywords
        ):
            matched_threads.append((thread_title, thread_url))
    print(f"Catalog中共获取到 {len(all_threads)} 个Threads")
    print(f"按关键字匹配到 {len(matched_threads)} 个Threads")
    if not matched_threads:
        raise SystemExit(0)
    for _, thread_url in matched_threads:
        SingleThreadDownloader4chan(
            thread_url,
            download_folder=download_folder,
            title_keywords=title_keywords,
        ).run()
