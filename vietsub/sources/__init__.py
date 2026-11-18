"""Video sources: Douyin and Kuaishou through the automation browser, other sites through yt-dlp.

Modules:
    base           shared types, errors and URL helpers
    browser        the automation browser (Chrome by default) and its own profile
    douyin         Douyin links, share page, aweme metadata and the browser page capture
    web            TikTok, YouTube and Bilibili links
    registry       picks the source for pasted text
    http_download  streaming download with progress, resume and cancel
    cookies        Cookie header, cookies.txt and browser cookie helpers
    ytdlp_source   yt-dlp wrapper with progress hooks and cancel
    local          validation of video files picked on this computer
"""
