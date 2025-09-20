import os
import random
import subprocess
import time

import requests
from lxml import etree
from tqdm import tqdm


def gebooru_downloader(page_range, download_folder, history_urls, history_handler, tags):
    headers = {
        "Cookie": "ADNF=a642803401bb64e8eda3f18ee36a7435; __utma=52483902.408065444.1446641088.1492218118.1492355795.289; _ga=GA1.2.408065444.1446641088; user_id=319612; pass_hash=f2ad63ed1614fcddb823dd9fa188feec8fc7c5e6; __cfduid=d6835714e71f517ccc437fddbf831d7191519553182; _gid=GA1.2.770386208.1537882525; resize-original=1; resize-notification=1; PHPSESSID=4qi1tkndb7ba9g142inms735p6; Hm_lvt_ba7c84ce230944c13900faeba642b2b4=1536924425,1537143399,1537882525,1537960936; gelcomPoop=1; Hm_lpvt_ba7c84ce230944c13900faeba642b2b4=1537961304",
        "Referer": "https://gelbooru.com/index.php?page=post&s=list&tags=animated&pid=84",
        "User-Agent": "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/69.0.3497.100 Safari/537.36",
    }
    proxies = {"http": "socks5://127.0.0.1:10808", "https": "socks5://127.0.0.1:10808"}

    base_url = f"https://gelbooru.com/index.php?page=post&s=list&tags={tags}&pid={{}}"

    # 创建以tags名称命名的子文件夹
    # 替换特殊字符，确保文件夹名称合法
    safe_tags = tags.replace("+", "_").replace(":", "_").replace("/", "_")
    tags_download_folder = os.path.join(download_folder, safe_tags)
    if not os.path.exists(tags_download_folder):
        os.makedirs(tags_download_folder)
        print(f"创建文件夹: {tags_download_folder}")

    def retry_request(url, max_retries=None, backoff_factor=1):
        """带重试机制的请求函数"""
        retry_count = 0
        while True:
            try:
                return requests.get(url, headers=headers, proxies=proxies, timeout=30)
            except Exception as e:
                retry_count += 1
                if max_retries is not None and retry_count >= max_retries:
                    raise e

                # 指数退避策略，随机延迟避免请求过于集中
                sleep_time = backoff_factor * (2 ** (retry_count - 1)) + random.uniform(0, 1)
                print(f"请求失败，{sleep_time:.2f}秒后重试 (第{retry_count}次): {e}")
                time.sleep(sleep_time)

    # 连续空页计数器，用于检测是否已经到达最后一页
    consecutive_empty_pages = 0
    max_empty_pages = 2  # 连续2页为空则认为已到达最后一页

    for page_num in range(page_range):
        try:
            print(f"正在处理第 {page_num + 1}/{page_range} 页...")
            r_page = retry_request(base_url.format(page_num * 42), max_retries=5)
            html = etree.HTML(r_page.content)
            item_urls = html.xpath("//div[@class='thumbnail-container']/article[@class='thumbnail-preview']//a/@href")
            print(f"第 {page_num + 1} 页找到 {len(item_urls)} 个项目")

            # 如果当前页为空，增加空页计数器
            if len(item_urls) == 0:
                consecutive_empty_pages += 1
                print(f"连续 {consecutive_empty_pages} 页为空")

                # 如果连续空页达到阈值，认为已到达最后一页
                if consecutive_empty_pages >= max_empty_pages:
                    print(f"连续 {max_empty_pages} 页为空，认为已到达最后一页，停止下载")
                    break
            else:
                # 如果当前页不为空，重置空页计数器
                consecutive_empty_pages = 0

        except Exception as e:
            print(f"获取第 {page_num + 1} 页失败: {e}")
            continue

        # 过滤掉已经在历史记录中的项目
        new_items = []
        for item_url in item_urls:
            if item_url not in history_urls:
                new_items.append(item_url)
                history_urls.add(item_url)
                history_handler.dump(history_urls)

        # 如果没有新项目，跳过当前页
        if not new_items:
            continue

        # 创建当前页面的进度条
        page_pbar = tqdm(total=len(new_items), desc=f"第 {page_num + 1} 页", unit="file")

        for item_url in new_items:
            try:
                r_item = retry_request(item_url, max_retries=5)
                item_html = etree.HTML(r_item.content)
                video_url = item_html.xpath("//a[text()='Original image']/@href")

                if len(video_url) > 0:
                    video_url = video_url[0]
                    if video_url in history_urls:
                        page_pbar.update(1)
                        continue
                    else:
                        file_name = video_url.split("/")[-1]

                        # 无限重试下载图片
                        while True:
                            try:
                                data = retry_request(video_url).content
                                break
                            except Exception as e:
                                print(f"下载失败，重试中: {file_name}")
                                time.sleep(5)

                        with open(os.path.join(tags_download_folder, file_name), "wb") as f:
                            f.write(data)

                        if os.path.splitext(file_name)[-1] in [".xxx"]:
                            subprocess.call(
                                'ffmpeg -i "{}" -f webm "{}.webm"'.format(
                                    os.path.join(tags_download_folder, file_name),
                                    os.path.join(tags_download_folder, os.path.splitext(file_name)[0]),
                                ),
                                shell=True,
                            )
                            os.remove(os.path.join(tags_download_folder, file_name))
                        history_urls.add(video_url)
                        history_handler.dump(history_urls)

                        # 更新页面进度条
                        page_pbar.update(1)
            except Exception as e:
                print(f"处理项目时出错: {e}")
                continue

        # 关闭页面进度条
        page_pbar.close()


if __name__ == "__main__":
    import argparse
    import sys

    from pickle_handler import PickleHandler

    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description="从Gelbooru下载图片或视频")
    parser.add_argument(
        "--tags", type=str, required=True, help="要下载的标签，可以使用+号连接多个标签，例如：tailbox+animated 或 user:作者名+animated"
    )
    parser.add_argument("--pages", type=int, default=99, help="要下载的页数，默认为99")
    parser.add_argument("--folder", type=str, default="./gelbooru_downloads", help="下载文件夹，默认为./gelbooru_downloads")

    args = parser.parse_args()

    # 创建下载文件夹
    if not os.path.exists(args.folder):
        os.makedirs(args.folder)

    # 初始化历史记录处理器
    history_handler = PickleHandler("wallpaper.history")
    history_urls = history_handler.load()

    print(f"开始下载标签为 {args.tags} 的内容...")
    gebooru_downloader(
        page_range=args.pages, download_folder=args.folder, history_urls=history_urls, history_handler=history_handler, tags=args.tags
    )

    print("下载完成！")
