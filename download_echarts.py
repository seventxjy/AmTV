import os
import requests

# 创建静态文件目录
os.makedirs('static/css', exist_ok=True)
os.makedirs('static/js', exist_ok=True)
os.makedirs('static/fonts', exist_ok=True)

# 下载Font Awesome CSS
print("正在下载Font Awesome...")
fa_css_url = "https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/css/font-awesome.min.css"
with open('static/css/font-awesome.min.css', 'wb') as f:
    f.write(requests.get(fa_css_url).content)

# 下载Font Awesome字体文件（5个文件）
font_files = [
    "FontAwesome.otf",
    "fontawesome-webfont.eot",
    "fontawesome-webfont.svg",
    "fontawesome-webfont.ttf",
    "fontawesome-webfont.woff",
    "fontawesome-webfont.woff2"
]
for font in font_files:
    font_url = f"https://cdn.jsdelivr.net/npm/font-awesome@4.7.0/fonts/{font}"
    with open(f'static/fonts/{font}', 'wb') as f:
        f.write(requests.get(font_url).content)

# 下载ECharts
print("正在下载ECharts...")
echarts_url = "https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"
with open('static/js/echarts.min.js', 'wb') as f:
    f.write(requests.get(echarts_url).content)

print("所有资源下载完成！")
