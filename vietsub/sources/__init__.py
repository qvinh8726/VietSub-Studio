"""Video sources: Douyin through the user's Edge session, other sites through yt-dlp.

Modules:
    base           shared types, errors and URL helpers
    douyin         Douyin links, aweme metadata and the Edge page capture
    web            TikTok, YouTube and Bilibili links
    registry       picks the source for pasted text
    http_download  streaming download with progress, resume and cancel
    cookies        Cookie header, cookies.txt and browser cookie helpers
    ytdlp_source   yt-dlp wrapper with progress hooks and cancel
    local          validation of video files picked on this computer
"""
