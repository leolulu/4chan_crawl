import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
import traceback
from typing import Optional

import lxml.etree as etree
import requests


class SingleThreadDownloader4chan:
    counter = 0
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
        title_word_keywords: Optional[list[str]] = None,
        catalog_title: Optional[str] = None,
    ) -> None:
        SingleThreadDownloader4chan.counter += 1
        self.thread_url = thread_url
        # 从URL中提取board名称
        self.board = self._extract_board_from_url(thread_url)
        self.thread_id = self._extract_thread_id_from_url(thread_url)
        self.target_formats = target_formats.split(",") if target_formats else target_formats
        self.title_keywords = [keyword.casefold() for keyword in title_keywords] if title_keywords else None
        self.title_word_keywords = title_word_keywords if title_word_keywords else None
        self.catalog_title = catalog_title.strip() if catalog_title and catalog_title.strip() else None
        self.download_folder = download_folder if download_folder else "./4chan_thread_download_folder"
        if not os.path.exists(self.download_folder):
            os.makedirs(self.download_folder)
        self.history_db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "download_history.sqlite3")
        self._init_history_db()
        print(f"[历史筛选] 初始化数据库: {self.history_db_path} (thread_id={self.thread_id})")
        self.pre_download_list = []

    def _extract_board_from_url(self, url: str) -> str:
        """从thread URL中提取board名称"""
        # URL格式类似: https://boards.4chan.org/board/thread/thread_id
        match = re.search(r"https?://boards\.4chan\.org/([^/]+)/thread/", url)
        if match:
            return match.group(1)
        else:
            raise ValueError("无法从URL中提取board名称")

    def _extract_thread_id_from_url(self, url: str) -> str:
        match = re.search(r"https?://boards\.4chan\.org/[^/]+/thread/(\d+)", url)
        if match:
            return match.group(1)
        else:
            raise ValueError("无法从URL中提取thread id")

    @staticmethod
    def request_get_with_retry(
        url: str,
        headers: Optional[dict[str, str]] = None,
        proxies: Optional[dict[str, str]] = None,
    ):
        final_headers = SingleThreadDownloader4chan.HEADER if headers is None else headers
        final_proxies = SingleThreadDownloader4chan.PROXIES if proxies is None else proxies
        attempts = 10
        for attempt in range(1, attempts + 1):
            try:
                response = requests.get(url, headers=final_headers, proxies=final_proxies)
                response.raise_for_status()
                return response
            except requests.RequestException:
                if attempt == attempts:
                    raise
                time.sleep(2)
        raise RuntimeError("请求重试次数耗尽")

    def _init_history_db(self):
        conn = sqlite3.connect(self.history_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS download_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    thread_id TEXT NOT NULL,
                    file_name TEXT NOT NULL,
                    file_md5 TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    saved_path TEXT NOT NULL,
                    removed_by_md5 INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(thread_id, file_name)
                )
                """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_download_history_thread_md5
                ON download_history(thread_id, file_md5)
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS thread_folder_mapping (
                    board TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    folder_name TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (board, thread_id)
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _get_thread_folder_mapping(self) -> Optional[str]:
        conn = sqlite3.connect(self.history_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT folder_name
                FROM thread_folder_mapping
                WHERE board = ? AND thread_id = ?
                LIMIT 1
                """,
                (self.board, self.thread_id),
            )
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _save_thread_folder_mapping(self, folder_name: str) -> None:
        conn = sqlite3.connect(self.history_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1
                FROM thread_folder_mapping
                WHERE board = ? AND thread_id = ?
                LIMIT 1
                """,
                (self.board, self.thread_id),
            )
            if cursor.fetchone():
                cursor.execute(
                    """
                    UPDATE thread_folder_mapping
                    SET folder_name = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE board = ? AND thread_id = ?
                    """,
                    (folder_name, self.board, self.thread_id),
                )
            else:
                cursor.execute(
                    """
                    INSERT INTO thread_folder_mapping
                    (board, thread_id, folder_name, created_at, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (self.board, self.thread_id, folder_name),
                )
            conn.commit()
        finally:
            conn.close()

    def _resolve_thread_folder_name(self, base_thread_name: str) -> str:
        mapped_folder_name = self._get_thread_folder_mapping()
        if mapped_folder_name:
            mapped_folder_path = os.path.join(self.download_folder, self.board, mapped_folder_name)
            if not os.path.exists(mapped_folder_path):
                print(
                    f"[目录映射-校验] 历史目录不存在，将重建该目录: thread_id={self.thread_id}, folder={mapped_folder_name}, path={mapped_folder_path}"
                )
            print(f"[目录映射-命中] 使用历史目录: thread_id={self.thread_id}, folder={mapped_folder_name}")
            return mapped_folder_name
        generated_folder_name = f"{SingleThreadDownloader4chan.counter:02d}.{base_thread_name}"
        self._save_thread_folder_mapping(generated_folder_name)
        print(f"[目录映射-新建] 首次记录目录映射: thread_id={self.thread_id}, folder={generated_folder_name}")
        return generated_folder_name

    def _history_exists_by_filename(self, file_name: str) -> bool:
        conn = sqlite3.connect(self.history_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT 1
                FROM download_history
                WHERE thread_id = ? AND file_name = ?
                LIMIT 1
                """,
                (self.thread_id, file_name),
            )
            return cursor.fetchone() is not None
        finally:
            conn.close()

    def _history_find_by_md5(self, file_md5: str) -> Optional[str]:
        conn = sqlite3.connect(self.history_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT file_name
                FROM download_history
                WHERE thread_id = ? AND file_md5 = ?
                LIMIT 1
                """,
                (self.thread_id, file_md5),
            )
            row = cursor.fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def _history_save_record(
        self,
        file_name: str,
        file_md5: str,
        image_url: str,
        saved_path: str,
        removed_by_md5: int,
    ) -> None:
        conn = sqlite3.connect(self.history_db_path)
        try:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO download_history
                (thread_id, file_name, file_md5, image_url, saved_path, removed_by_md5, created_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    self.thread_id,
                    file_name,
                    file_md5,
                    image_url,
                    saved_path,
                    removed_by_md5,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _calculate_file_md5(file_path: str) -> str:
        md5_hash = hashlib.md5()
        with open(file_path, "rb") as file_obj:
            for chunk in iter(lambda: file_obj.read(8192), b""):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()

    @staticmethod
    def _match_word_boundary_keyword(title_text: str, keyword: str) -> bool:
        return (
            re.search(
                rf"(?<!\w){re.escape(keyword)}(?!\w)",
                title_text,
                flags=re.IGNORECASE,
            )
            is not None
        )

    def _extract_display_title_from_thread_page(self, html_root, page_text: str) -> str:
        subject_candidates = []
        if html_root is not None:
            for text in html_root.xpath(".//span[@class='subject']/text()"):
                cleaned = (text or "").strip()
                if cleaned:
                    subject_candidates.append(cleaned)
        if subject_candidates:
            return max(subject_candidates, key=len)

        title_match = re.search(r"<title>(.*?)</title>", page_text, re.S)
        if not title_match:
            return ""
        raw_title = title_match.group(1).strip()
        prefix = f"/{self.board}/ - "
        if raw_title.startswith(prefix):
            raw_title = raw_title[len(prefix) :]
        suffix = " - Adult GIF - 4chan"
        if raw_title.endswith(suffix):
            raw_title = raw_title[: -len(suffix)]
        return raw_title.strip()

    @staticmethod
    def _sanitize_title_for_folder_name(raw_title: str) -> str:
        return (
            raw_title.replace("|", "")
            .replace("?", "")
            .replace("*", "")
            .replace("#", "")
            .replace("\\", "")
            .replace("<", "")
            .replace(">", "")
            .replace(":", "")
            .replace('"', "")
            .replace("“", "")
            .replace("/", "")
            .strip()
        )

    def _title_matches_filters(self, title_text: str) -> bool:
        contains_match = False
        word_boundary_match = False
        if self.title_keywords:
            folded_title = title_text.casefold()
            contains_match = any(keyword in folded_title for keyword in self.title_keywords)
        if self.title_word_keywords:
            word_boundary_match = any(self._match_word_boundary_keyword(title_text, keyword) for keyword in self.title_word_keywords)
        if self.title_keywords is None and self.title_word_keywords is None:
            return True
        return contains_match or word_boundary_match

    def parse_thread_get_img_url(self):
        """解析单个thread获取图片URL"""
        try:
            r = self.request_get_with_retry(self.thread_url)
            html = etree.HTML(r.content)
            if html is None:
                html = etree.HTML(r.text)
            display_title = self._extract_display_title_from_thread_page(html, r.text)
            title_for_filter = self.catalog_title or display_title
            if not self._title_matches_filters(title_for_filter):
                print(f"跳过帖子(标题不匹配): {display_title or title_for_filter}")
                return
            print(f"解析帖子: {display_title or title_for_filter}")
            folder_base_title = self._sanitize_title_for_folder_name(display_title or title_for_filter or self.thread_id)
            if not folder_base_title:
                folder_base_title = self.thread_id
            thread_name = self._resolve_thread_folder_name(folder_base_title)
            print(f"[目录路由] 最终下载目录: thread_id={self.thread_id}, folder={thread_name}")
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
            print(f"[前筛] 检查文件名历史: thread_id={self.thread_id}, file_name={f_name}")
            if self._history_exists_by_filename(f_name):
                print(f"[前筛-命中] 历史已存在，跳过下载: thread_id={self.thread_id}, file_name={f_name}")
                return
            print(f"[前筛-通过] 文件名未命中历史，开始下载: thread_id={self.thread_id}, file_name={f_name}")
            download_folder = os.path.join(self.download_folder, self.board, thread_name)
            if not os.path.exists(download_folder):
                os.makedirs(download_folder)
            file_path = os.path.join(download_folder, f_name)
            content = self.request_get_with_retry(img).content
            with open(file_path, "wb") as f:
                f.write(content)
            print(f"[下载] 下载完成: thread_id={self.thread_id}, file_name={f_name}, path={file_path}")
            print(f"[后筛] 开始MD5计算: thread_id={self.thread_id}, file_name={f_name}")
            file_md5 = self._calculate_file_md5(file_path)
            print(f"[后筛] MD5计算完成: thread_id={self.thread_id}, file_name={f_name}, md5={file_md5}")
            md5_hit_file_name = self._history_find_by_md5(file_md5)
            if md5_hit_file_name:
                print(
                    f"[后筛-命中] MD5已存在，判定重复内容: thread_id={self.thread_id}, file_name={f_name}, 历史文件名={md5_hit_file_name}, md5={file_md5}"
                )
                os.remove(file_path)
                self._history_save_record(
                    file_name=f_name,
                    file_md5=file_md5,
                    image_url=img,
                    saved_path="",
                    removed_by_md5=1,
                )
                print(f"[后筛-剔除] 已删除重复文件并记录历史: thread_id={self.thread_id}, file_name={f_name}, md5={file_md5}")
                return
            self._history_save_record(
                file_name=f_name,
                file_md5=file_md5,
                image_url=img,
                saved_path=file_path,
                removed_by_md5=0,
            )
            print(f"[后筛-通过] MD5未命中历史，保留文件并写入历史: thread_id={self.thread_id}, file_name={f_name}, md5={file_md5}")
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
    catalog_match = re.search(r"var catalog = (\{.*?\});var style_group", response.text, re.S)
    if not catalog_match:
        raise ValueError("无法从catalog页面提取thread数据")
    catalog_data = json.loads(catalog_match.group(1))
    slug = catalog_data.get("slug", "gif")
    threads_data = catalog_data.get("threads", {})
    threads = []
    for thread_id, thread_info in threads_data.items():
        thread_title = (thread_info.get("sub") or thread_info.get("teaser") or "").strip()
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
        required=False,
        metavar="KEYWORD",
        help=(
            "按标题关键字筛选(不区分大小写)，可重复传入多个\n"
            "匹配规则: 命中任意一个关键字(OR)即下载\n"
            "示例: --title-keyword milf --title-keyword bbw\n"
            "输入清洗: 自动去首尾空格、去重、忽略空值"
        ),
    )
    parser.add_argument(
        "--title-word-keyword",
        action="append",
        required=False,
        metavar="KEYWORD",
        help=(
            "按标题单词边界筛选(不区分大小写)，可重复传入多个\n"
            "匹配规则: 关键字必须命中单词边界(完整单词/句首/句尾)才下载\n"
            "示例: --title-word-keyword cat --title-word-keyword milf\n"
            "输入清洗: 自动去首尾空格、去重、忽略空值"
        ),
    )
    parser.add_argument(
        "--exclude-keyword",
        action="append",
        required=False,
        metavar="KEYWORD",
        help=(
            "按标题关键字剔除(不区分大小写)，可重复传入多个\n"
            "匹配规则: 命中任意一个关键字(OR)即剔除，优先级最高\n"
            "示例: --exclude-keyword gay --exclude-keyword tranny\n"
            "输入清洗: 自动去首尾空格、去重、忽略空值"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        required=False,
        help="只测试筛选逻辑，不实际下载图片",
    )
    args = parser.parse_args()
    download_folder = args.download_folder

    def normalize_keywords(raw_keywords: Optional[list[str]]) -> list[str]:
        normalized_keywords = []
        seen_keywords = set()
        if not raw_keywords:
            return normalized_keywords
        for keyword in raw_keywords:
            cleaned_keyword = keyword.strip()
            if not cleaned_keyword:
                continue
            folded_keyword = cleaned_keyword.casefold()
            if folded_keyword in seen_keywords:
                continue
            seen_keywords.add(folded_keyword)
            normalized_keywords.append(cleaned_keyword)
        return normalized_keywords

    title_keywords = normalize_keywords(args.title_keyword)
    title_word_keywords = normalize_keywords(args.title_word_keyword)
    exclude_keywords = normalize_keywords(args.exclude_keyword)
    if not title_keywords and not title_word_keywords:
        raise SystemExit("错误: 至少需要一个非空筛选参数: --title-keyword 或 --title-word-keyword")
    catalog_url = "https://boards.4chan.org/gif/catalog"
    all_threads = fetch_catalog_threads(catalog_url)
    matched_threads = []
    for thread_title, thread_url in all_threads:
        # 先检查剔除条件（优先级最高）
        if exclude_keywords:
            folded_title = thread_title.casefold()
            if any(keyword.casefold() in folded_title for keyword in exclude_keywords):
                print(f"[剔除] 标题命中排除关键字: {thread_title}")
                continue

        contains_match = False
        word_boundary_match = False
        if title_keywords:
            folded_title = thread_title.casefold()
            contains_match = any(keyword.casefold() in folded_title for keyword in title_keywords)
        if title_word_keywords:
            word_boundary_match = any(
                SingleThreadDownloader4chan._match_word_boundary_keyword(thread_title, keyword) for keyword in title_word_keywords
            )
        if contains_match or word_boundary_match:
            matched_threads.append((thread_title, thread_url))
    print(f"Catalog中共获取到 {len(all_threads)} 个Threads")
    print(f"按关键字匹配到 {len(matched_threads)} 个Threads")
    if not matched_threads:
        raise SystemExit(0)

    # dry-run 模式：只显示匹配结果，不下载
    if args.dry_run:
        print("\n" + "=" * 60)
        print("[DRY-RUN 模式] 仅显示筛选结果，不执行下载")
        print("=" * 60)
        print(f"\n筛选参数:")
        print(f"  - 包含关键字: {title_keywords if title_keywords else '无'}")
        print(f"  - 单词边界: {title_word_keywords if title_word_keywords else '无'}")
        print(f"  - 排除关键字: {exclude_keywords if exclude_keywords else '无'}")
        print(f"\n匹配到的 {len(matched_threads)} 个Threads:")
        for i, (title, url) in enumerate(matched_threads, 1):
            print(f"  {i}. {title}")
            print(f"     {url}")
        print("\n" + "=" * 60)
        print("[DRY-RUN 完成] 以上仅为预览，未实际下载")
        print("=" * 60)
        raise SystemExit(0)

    for thread_title, thread_url in matched_threads:
        SingleThreadDownloader4chan(
            thread_url,
            download_folder=download_folder,
            title_keywords=title_keywords,
            title_word_keywords=title_word_keywords,
            catalog_title=thread_title,
        ).run()
