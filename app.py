import os
import json
import requests
import time
import uuid
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask.cli import with_appcontext
from flask_sqlalchemy import SQLAlchemy
import traceback
import shutil
from sqlalchemy import func
import random  # 导入随机数模块
from flask import session, flash, redirect, url_for, request

# 创建Flask应用
app = Flask(__name__)

# 配置应用
app.config['SECRET_KEY'] = 'your-secret-key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:123456@localhost/anime_db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 文件存储配置
app.config['UPLOAD_FOLDER'] = 'static/uploads'  # 用户上传的图像
app.config['OUTPUT_FOLDER'] = 'static/outputs'  # 自动生成的图像

# ComfyUI配置
COMFYUI_API_URL = 'http://127.0.0.1:8188'
WORKFLOW_JSON_PATH = 'C:\\动画爬虫\\anime.json'
COMFYUI_OUTPUT_DIR = 'D:\\ComfyUI_windows_portable_nvidia\\ComfyUI_windows_portable\\ComfyUI\\output'

# 从models.py导入数据库实例和模型
from models import db, User, Image, Anime, Rating, Tag, AnimeTag, Reply, Comment

# 初始化数据库
db.init_app(app)

# 确保存储目录存在
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)


# 首页路由
@app.route('/')
def index():
    search_query = request.args.get('search', '')
    sort = request.args.get('sort', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20

    # 基础查询
    query = Anime.query

    # 搜索功能
    if search_query:
        query = query.filter(Anime.name.like(f'%{search_query}%'))

    # 排序处理（只保留降序）
    if sort == 'rating':
        # 按总评分降序
        query = query.outerjoin(Rating).group_by(Anime.id).order_by(func.avg(Rating.score).desc())
    elif sort == 'direction':
        # 按演出评分降序
        query = query.outerjoin(Rating).group_by(Anime.id).order_by(func.avg(Rating.direction).desc())
    elif sort == 'animation':
        # 按作画评分降序
        query = query.outerjoin(Rating).group_by(Anime.id).order_by(func.avg(Rating.animation).desc())
    elif sort == 'story':
        # 按剧情评分降序
        query = query.outerjoin(Rating).group_by(Anime.id).order_by(func.avg(Rating.story).desc())
    elif sort == 'music':
        # 按音乐评分降序
        query = query.outerjoin(Rating).group_by(Anime.id).order_by(func.avg(Rating.music).desc())
    elif sort == 'voice_acting':
        # 按配音评分降序
        query = query.outerjoin(Rating).group_by(Anime.id).order_by(func.avg(Rating.voice_acting).desc())

    # 执行分页查询
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    animes = pagination.items

    # 批量获取评分数据
    if animes:
        anime_ids = [anime.id for anime in animes]
        rating_data = db.session.query(
            Rating.anime_id,
            func.count(Rating.id).label('rating_count'),
            func.avg(Rating.score).label('avg_rating')
        ).filter(Rating.anime_id.in_(anime_ids)) \
            .group_by(Rating.anime_id) \
            .all()

        rating_map = {data.anime_id: (data.rating_count, data.avg_rating) for data in rating_data}
        for anime in animes:
            anime.rating_count, anime.avg_rating = rating_map.get(anime.id, (0, 0))

    return render_template('index.html',
                           animes=animes,
                           pagination=pagination,
                           search_query=search_query,
                           sort=sort)


@app.route('/generate', methods=['GET', 'POST'])
def generate():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # 初始化变量，确保在GET请求时也有默认值
    positive_prompt = ''
    negative_prompt = ''
    upload = False
    generated = False
    image_url = ''
    seed = None
    button_disabled = False

    if request.method == 'POST':
        positive_prompt = request.form.get('positive_prompt', '')
        negative_prompt = request.form.get('negative_prompt', '')
        upload = request.form.get('upload', 'no') == 'yes'

        if not positive_prompt:
            flash('请输入正向提示词', 'error')
            return redirect(url_for('generate'))

        try:
            # 读取工作流文件
            if not os.path.exists(WORKFLOW_JSON_PATH):
                raise FileNotFoundError(f'工作流文件不存在: {WORKFLOW_JSON_PATH}')

            with open(WORKFLOW_JSON_PATH, 'r', encoding='utf-8-sig') as f:
                workflow = json.load(f)

            # 获取节点字典
            nodes = workflow

            # 修复节点class_type
            def fix_node_class_type(node):
                if not isinstance(node, dict):
                    return node
                if 'class_type' not in node:
                    if 'type' in node:
                        node['class_type'] = node['type']
                    else:
                        node['class_type'] = 'Unknown'
                return node

            for node_id in list(nodes.keys()):
                if node_id.startswith('#'):
                    continue
                nodes[node_id] = fix_node_class_type(nodes[node_id])

            # 查找文本编码节点
            text_encode_nodes = [
                node for node_id, node in nodes.items()
                if node.get('class_type') == 'CLIPTextEncode'
            ]

            if len(text_encode_nodes) < 2:
                raise ValueError('工作流中未找到足够的文本编码节点')

            # 匹配正负提示词节点
            positive_node = text_encode_nodes[0]
            negative_node = text_encode_nodes[1] if len(text_encode_nodes) > 1 else None

            if not positive_node or not negative_node:
                raise ValueError('无法确定正向和负向提示词节点')

            # 更新提示词
            positive_node['inputs']['text'] = positive_prompt
            negative_node['inputs']['text'] = negative_prompt

            # 生成随机种子 (使用64位整数，符合ComfyUI种子格式)
            random_seed = random.getrandbits(64)
            print(f"生成的随机种子: {random_seed}")

            # 查找KSampler节点并设置随机种子
            ksampler_node = None
            for node_id, node in nodes.items():
                if node.get('class_type') == 'KSampler':
                    ksampler_node = node
                    break

            if ksampler_node:
                # 更新种子值
                ksampler_node['inputs']['seed'] = random_seed
                print(f"已设置KSampler种子: {random_seed}")
            else:
                print("警告: 未找到KSampler节点，无法设置随机种子")

            # 构建API请求
            api_payload = {'prompt': nodes}

            # 发送请求到ComfyUI
            response = requests.post(
                f'{COMFYUI_API_URL}/prompt',
                json=api_payload,
                timeout=300
            )
            response.raise_for_status()
            prompt_data = response.json()

            # 处理生成结果
            prompt_id = prompt_data.get('prompt_id')
            if not prompt_id:
                raise ValueError('ComfyUI未返回有效的任务ID')

            print(f"ComfyUI任务ID: {prompt_id}")

            # 轮询任务状态
            max_attempts = 60
            for attempt in range(max_attempts):
                time.sleep(5)
                status_response = requests.get(f'{COMFYUI_API_URL}/history/{prompt_id}')
                status_response.raise_for_status()
                history_data = status_response.json()

                if prompt_id in history_data:
                    output_images = history_data[prompt_id].get('outputs', {})
                    if not output_images:
                        raise ValueError('任务完成但未生成图像')

                    image_filename = None
                    for node_id, outputs in output_images.items():
                        if isinstance(outputs, dict) and 'images' in outputs:
                            image_data = outputs['images'][0]
                            image_filename = image_data.get('filename')
                            break

                    if not image_filename:
                        raise ValueError('无法从输出中找到图像文件')

                    # 处理图像文件
                    comfyui_image_path = os.path.join(COMFYUI_OUTPUT_DIR, image_filename)
                    if not os.path.exists(comfyui_image_path):
                        raise FileNotFoundError(f'图像文件不存在: {comfyui_image_path}')

                    # 保存到项目目录
                    os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
                    output_filename = "latest_generated.png"
                    output_path = os.path.join(app.config['OUTPUT_FOLDER'], output_filename)
                    shutil.copy2(comfyui_image_path, output_path)

                    # 更新状态变量
                    generated = True
                    image_url = url_for('static', filename=f'outputs/{output_filename}', _external=True)
                    seed = random_seed

                    # 处理用户保存请求
                    if upload:
                        unique_filename = f"uploaded_{uuid.uuid4().hex}_{datetime.now().strftime('%Y%m%d%H%M%S')}.png"
                        upload_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
                        shutil.copy2(comfyui_image_path, upload_path)

                        new_image = Image(
                            filename=unique_filename,
                            url=url_for('static', filename=f'uploads/{unique_filename}', _external=True),
                            user_id=session['user_id'],
                            prompt=positive_prompt,
                            negative_prompt=negative_prompt
                        )
                        db.session.add(new_image)
                        db.session.commit()
                        flash('插画生成并保存成功！', 'success')
                        return redirect(url_for('my_images'))

                    # 返回生成结果
                    return render_template(
                        'generate.html',
                        generated=generated,
                        image_url=image_url,
                        positive_prompt=positive_prompt,
                        negative_prompt=negative_prompt,
                        upload=upload,
                        seed=seed,
                        button_disabled=False
                    )

            raise TimeoutError(f'等待图像生成超时（{max_attempts * 5}秒）')

        except Exception as e:
            flash(f'生成失败: {str(e)}', 'error')
            traceback.print_exc()
            return redirect(url_for('generate'))

    # 处理GET请求
    return render_template(
        'generate.html',
        generated=generated,
        image_url=image_url,
        positive_prompt=positive_prompt,
        negative_prompt=negative_prompt,
        upload=upload,
        seed=seed,
        button_disabled=button_disabled
    )


@app.route('/save_generated_image', methods=['POST'])
def save_generated_image():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'}), 401

    try:
        data = request.json
        positive_prompt = data.get('prompt', '')
        negative_prompt = data.get('negative_prompt', '')

        latest_image = os.path.join(app.config['OUTPUT_FOLDER'], "latest_generated.png")
        if not os.path.exists(latest_image):
            return jsonify({'success': False, 'message': '未找到最新生成的图像'}), 404

        unique_filename = f"uploaded_{uuid.uuid4().hex}_{datetime.now().strftime('%Y%m%d%H%M%S')}.png"
        upload_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
        shutil.copy2(latest_image, upload_path)

        new_image = Image(
            filename=unique_filename,
            url=url_for('static', filename=f'uploads/{unique_filename}', _external=True),
            user_id=session['user_id'],
            prompt=positive_prompt,
            negative_prompt=negative_prompt
        )
        db.session.add(new_image)
        db.session.commit()

        return jsonify({'success': True, 'message': '图像保存成功'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/my_images', methods=['GET'])
def my_images():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # 获取当前用户ID
    user_id = session['user_id']

    # 获取页码，默认为第1页
    page = request.args.get('page', 1, type=int)
    per_page = 12  # 每页显示12张图片

    # 查询当前用户的图片并进行分页
    images = Image.query.filter_by(user_id=user_id).order_by(Image.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )

    return render_template('my_images.html', images=images)


# 删除插画
@app.route('/delete_image/<int:image_id>', methods=['GET', 'POST'])
def delete_image(image_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    image = Image.query.get_or_404(image_id)

    if image.user_id != session['user_id']:
        flash('您无权删除此图像', 'error')
        return redirect(url_for('image_detail', image_id=image_id))

    # 删除文件
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], image.filename)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            flash(f'删除文件时出错: {str(e)}', 'error')
            return redirect(url_for('image_detail', image_id=image_id))

    # 从数据库删除
    db.session.delete(image)
    db.session.commit()

    flash('图像已成功删除', 'success')
    return redirect(url_for('gallery'))


@app.route('/gallery', methods=['GET'])
def gallery():
    # 分页查询所有插画
    page = request.args.get('page', 1, type=int)
    per_page = 12
    images = Image.query.order_by(Image.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    return render_template('gallery.html', images=images)


@app.route('/image_detail/<int:image_id>', methods=['GET', 'POST'])
def image_detail(image_id):
    """插画详情页面"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    # 获取指定ID的插画
    image = Image.query.get_or_404(image_id)

    # 检查用户是否有权限查看（如果是公开画廊则无需检查）
    # 这里设置为公开查看，如需权限控制可添加额外检查
    # if image.user_id != session['user_id'] and not current_user.is_admin:
    #     flash('您无权查看此插画', 'error')
    #     return redirect(url_for('gallery'))

    return render_template(
        'image_detail.html',
        image=image,
        current_user_id=session.get('user_id')
    )


@app.route('/download_image/<int:image_id>', methods=['GET'])
def download_image(image_id):
    """下载插画图片"""
    if 'user_id' not in session:
        return redirect(url_for('login'))

    image = Image.query.get_or_404(image_id)

    # 检查用户是否有权限下载
    if image.user_id != session['user_id']:
        flash('您无权下载此插画', 'error')
        return redirect(url_for('image_detail', image_id=image_id))

    # 构建图片完整路径
    image_path = os.path.join(app.config['UPLOAD_FOLDER'], image.filename)
    if not os.path.exists(image_path):
        flash('图片文件不存在', 'error')
        return redirect(url_for('image_detail', image_id=image_id))

    # 使用send_file实现下载
    from flask import send_file
    return send_file(
        image_path,
        as_attachment=True,
        download_name=image.filename
    )


# 动画详情页路由
@app.route('/anime/<int:anime_id>')
def anime_detail(anime_id):
    anime = Anime.query.get_or_404(anime_id)

    # 计算当前动画各维度平均分
    from sqlalchemy import func
    rating_stats = db.session.query(
        func.count(Rating.id).label('rating_count'),
        func.avg(Rating.score).label('avg_score'),
        func.avg(Rating.animation).label('avg_animation'),
        func.avg(Rating.direction).label('avg_direction'),
        func.avg(Rating.story).label('avg_story'),
        func.avg(Rating.music).label('avg_music'),
        func.avg(Rating.voice_acting).label('avg_voice_acting')
    ).filter(Rating.anime_id == anime_id).first()

    # 处理无评分情况
    rating_count = rating_stats.rating_count or 0
    avg_rating = rating_stats.avg_score or 0
    avg_animation = rating_stats.avg_animation or 0
    avg_direction = rating_stats.avg_direction or 0
    avg_story = rating_stats.avg_story or 0
    avg_music = rating_stats.avg_music or 0
    avg_voice_acting = rating_stats.avg_voice_acting or 0

    # 新增：计算所有有评分动画的平均六维分数
    # 获取所有有评分的动画ID
    rated_anime_ids = db.session.query(Rating.anime_id).distinct().subquery()

    # 计算各维度平均值
    all_anime_avg = db.session.query(
        func.avg(Rating.score).label('overall'),
        func.avg(Rating.animation).label('animation'),
        func.avg(Rating.direction).label('direction'),
        func.avg(Rating.story).label('story'),
        func.avg(Rating.music).label('music'),
        func.avg(Rating.voice_acting).label('voice_acting')
    ).filter(Rating.anime_id.in_(rated_anime_ids)).first()

    # 转换为字典并保留一位小数
    all_anime_avg_data = {
        'overall': round(all_anime_avg.overall, 1) if all_anime_avg.overall else 0,
        'animation': round(all_anime_avg.animation, 1) if all_anime_avg.animation else 0,
        'direction': round(all_anime_avg.direction, 1) if all_anime_avg.direction else 0,
        'story': round(all_anime_avg.story, 1) if all_anime_avg.story else 0,
        'music': round(all_anime_avg.music, 1) if all_anime_avg.music else 0,
        'voice_acting': round(all_anime_avg.voice_acting, 1) if all_anime_avg.voice_acting else 0
    }

    # 获取前10个标签及其数量
    top_tags = db.session.query(
        Tag.name,
        func.sum(AnimeTag.count).label('count')
    ).join(
        AnimeTag, Tag.id == AnimeTag.tag_id
    ).filter(
        AnimeTag.anime_id == anime_id
    ).group_by(
        Tag.name
    ).order_by(
        func.sum(AnimeTag.count).desc()
    ).limit(10).all()

    # 查询当前动画的所有评论，按时间倒序排列
    comments = Comment.query.filter_by(anime_id=anime_id) \
        .order_by(Comment.created_at.desc()) \
        .all()
    # 预加载回复数据
    for comment in comments:
        comment.replies = Reply.query.filter_by(comment_id=comment.id) \
            .order_by(Reply.created_at.asc()) \
            .all()

    return render_template(
        'anime_detail.html',
        anime=anime,
        avg_rating=avg_rating,
        rating_count=rating_count,
        avg_animation=avg_animation,
        avg_direction=avg_direction,
        avg_story=avg_story,
        avg_music=avg_music,
        avg_voice_acting=avg_voice_acting,
        top_tags=top_tags,
        comments=comments,
        all_anime_avg=all_anime_avg_data  # 传递所有动画的平均评分数据
    )


# 注册路由
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        confirm_password = request.form['confirm_password']

        if User.query.filter_by(username=username).first():
            return render_template('register.html', error='用户名已存在')

        if password != confirm_password:
            return render_template('register.html', error='两次输入的密码不一致')

        new_user = User(
            username=username,
            password=password
        )

        db.session.add(new_user)
        db.session.commit()

        flash('注册成功，请登录')
        return redirect(url_for('login'))

    return render_template('register.html')


# 登录路由
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        user = User.query.filter_by(username=username).first()

        if user and user.password == password:
            session['user_id'] = user.id
            session['username'] = user.username
            flash('登录成功')
            return redirect(url_for('index'))
        else:
            return render_template('login.html',
                                   error='用户名或密码错误')

    return render_template('login.html')


# 登出路由
@app.route('/logout')
def logout():
    session.pop('user_id', None)
    session.pop('username', None)
    flash('已安全退出')
    return redirect(url_for('index'))


# 我的主页路由
@app.route('/mypage')
def mypage():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user = db.session.get(User, user_id)
    ratings = Rating.query.filter_by(user_id=user_id).all()

    # 获取评分对应的动画信息（包括图片）
    anime_ids = [rating.anime_id for rating in ratings]
    anime_map = {anime.id: anime for anime in Anime.query.filter(Anime.id.in_(anime_ids)).all()}

    return render_template('mypage.html', user=user, ratings=ratings, anime_map=anime_map)


@app.route('/user_profile')
def user_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user = db.session.get(User, user_id)

    # 获取用户的所有评分
    user_ratings = Rating.query.filter_by(user_id=user_id).all()

    if not user_ratings:
        return render_template('user_profile.html', user=user, message='您还没有评分过任何动画，无法生成用户画像')

    # 计算用户标签推荐度
    tag_recommendations = calculate_tag_recommendations(user_id)

    # 提取前20个和后10个标签
    sorted_tags = sorted(
        tag_recommendations.items(),
        key=lambda x: x[1],
        reverse=True
    )

    top_20_tags = sorted_tags[:20]
    bottom_10_tags = sorted_tags[-10:]

    # 合并标签列表
    selected_tags = top_20_tags + bottom_10_tags

    # 去重（如果有重叠的标签）
    unique_tags = []
    tag_names = set()
    for tag, score in selected_tags:
        if tag not in tag_names:
            tag_names.add(tag)
            unique_tags.append((tag, score))

    return render_template(
        'user_profile.html',
        user=user,
        top_tags=unique_tags,
        message=None
    )


def calculate_tag_recommendations(user_id):
    """计算用户的标签推荐度"""
    # 获取用户的所有评分
    user_ratings = Rating.query.filter_by(user_id=user_id).all()

    # 存储每个标签的推荐度
    tag_recommendation = {}

    # 遍历用户评分的每个动画
    for rating in user_ratings:
        anime_id = rating.anime_id
        score = rating.score

        # 获取该动画的前10个标签及其数量
        top_tags = db.session.query(
            Tag.name,
            func.sum(AnimeTag.count).label('count')
        ).join(
            AnimeTag, Tag.id == AnimeTag.tag_id
        ).filter(
            AnimeTag.anime_id == anime_id
        ).group_by(
            Tag.name
        ).order_by(
            func.sum(AnimeTag.count).desc()
        ).limit(10).all()

        # 计算标签数量总和
        total_count = sum(tag.count for tag in top_tags)

        # 计算每个标签的推荐度
        for tag in top_tags:
            tag_name = tag.name
            tag_count = tag.count

            # 计算标签数量占比
            percentage = tag_count / total_count if total_count > 0 else 0

            # 根据评分确定基础推荐度
            if score <= 2:
                base_recommendation = -2
            elif score <= 2.5:
                base_recommendation = -1
            elif score <= 3.5:
                base_recommendation = 0
            elif score <= 4:
                base_recommendation = 1
            elif score <= 5:
                base_recommendation = 2
            else:
                base_recommendation = 0

            # 计算标签推荐度 = 基础推荐度 * 标签数量占比
            tag_recommendation_score = base_recommendation * percentage

            # 累加相同标签的推荐度
            if tag_name in tag_recommendation:
                tag_recommendation[tag_name] += tag_recommendation_score
            else:
                tag_recommendation[tag_name] = tag_recommendation_score

    return tag_recommendation


# 动画评分页
@app.route('/rating')
def rating():
    search_query = request.args.get('search', '')
    page = request.args.get('page', 1, type=int)
    per_page = 20

    # 基础查询
    query = Anime.query

    # 搜索功能
    if search_query:
        query = query.filter(Anime.name.like(f'%{search_query}%'))

    # 执行分页查询
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    animes = pagination.items

    # 获取当前用户的评分
    user_ratings = {}
    if 'user_id' in session:
        user_id = session['user_id']
        ratings = Rating.query.filter_by(user_id=user_id).all()
        user_ratings = {rating.anime_id: rating.score for rating in ratings}

    return render_template('rating.html',
                           animes=animes,
                           user_ratings=user_ratings,
                           pagination=pagination,
                           search_query=search_query)


# 获取评分详情（用于弹窗加载）
@app.route('/get_rating/<int:rating_id>')
def get_rating(rating_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '未登录'})

    rating = Rating.query.filter_by(id=rating_id, user_id=session['user_id']).first()
    if not rating:
        return jsonify({'success': False, 'message': '评分不存在'})

    return jsonify({
        'success': True,
        'rating': {
            'animation': rating.animation,
            'direction': rating.direction,
            'voice_acting': rating.voice_acting,
            'music': rating.music,
            'story': rating.story
        }
    })


# 提交评分
@app.route('/submit_rating', methods=['POST'])
def submit_rating():
    if 'user_id' not in session:
        flash('请先登录', 'error')
        return redirect(url_for('login'))

    anime_id = request.form.get('anime_id')
    rating_id = request.form.get('rating_id')
    page = request.form.get('page', 1)
    search = request.form.get('search', '')

    # 获取各维度评分
    try:
        animation = int(request.form.get('animation'))
        direction = int(request.form.get('direction'))
        voice_acting = int(request.form.get('voice_acting'))
        music = int(request.form.get('music'))
        story = int(request.form.get('story'))

        # 验证评分范围
        for score in [animation, direction, voice_acting, music, story]:
            if score < 1 or score > 5:
                raise ValueError("评分必须在1-5之间")

    except (ValueError, TypeError) as e:
        flash(f'评分数据无效: {str(e)}', 'error')
        return redirect(url_for('rating', page=page, search=search))

    # 计算加权总分
    score = 0.4 * direction + 0.2 * animation + 0.2 * story + 0.1 * music + 0.1 * voice_acting

    # 检查是否是修改已有评分
    if rating_id:
        rating = Rating.query.filter_by(id=rating_id, user_id=session['user_id']).first()
        if rating:
            rating.animation = animation
            rating.direction = direction
            rating.voice_acting = voice_acting
            rating.music = music
            rating.story = story
            rating.score = score
            db.session.commit()
            flash('评分已更新', 'success')
            return redirect(url_for('rating', page=page, search=search))

    # 检查是否已评分
    existing_rating = Rating.query.filter_by(anime_id=anime_id, user_id=session['user_id']).first()
    if existing_rating:
        flash('您已对该动画评分', 'error')
        return redirect(url_for('rating', page=page, search=search))

    # 创建新评分
    new_rating = Rating(
        anime_id=anime_id,
        user_id=session['user_id'],
        animation=animation,
        direction=direction,
        voice_acting=voice_acting,
        music=music,
        story=story,
        score=score
    )

    db.session.add(new_rating)
    db.session.commit()
    flash('评分提交成功', 'success')
    return redirect(url_for('rating', page=page, search=search))


# 动画推荐页
@app.route('/recommend')
def recommend():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user = db.session.get(User, user_id)

    page = request.args.get('page', 1, type=int)
    per_page = 20
    max_recommendations = 100  # 最大推荐数量限制

    # 获取用户的所有评分
    user_ratings = Rating.query.filter_by(user_id=user_id).all()

    if not user_ratings:
        return render_template('recommend.html', message='您还没有评分过任何动画，无法为您推荐')

    # 计算推荐动画
    recommended_animes_with_scores = calculate_recommendations(user_id)

    # 取前100个推荐
    top_recommendations = recommended_animes_with_scores[:max_recommendations]

    # 计算总推荐数量（限制为60）
    total_recommendations = len(top_recommendations)

    # 分页处理
    start_index = (page - 1) * per_page
    end_index = min(start_index + per_page, total_recommendations)
    recommended_animes = [anime for anime, _ in top_recommendations[start_index:end_index]]

    # 为每个推荐动画获取评分数据
    anime_ratings = {}
    for anime in recommended_animes:
        rating_query = db.session.query(func.avg(Rating.score)).filter_by(anime_id=anime.id)
        anime_ratings[anime.id] = {
            'avg_rating': rating_query.scalar() or 0,
            'rating_count': db.session.query(func.count(Rating.id)).filter_by(anime_id=anime.id).scalar() or 0
        }

    return render_template(
        'recommend.html',
        user=user,
        recommended_animes=recommended_animes,
        anime_ratings=anime_ratings,
        message=None,
        pagination={
            'page': page,
            'per_page': per_page,
            'total': total_recommendations,
            'has_prev': page > 1,
            'has_next': end_index < total_recommendations,
            'prev_num': page - 1 if page > 1 else None,
            'next_num': page + 1 if end_index < total_recommendations else None,
            'pages': (total_recommendations + per_page - 1) // per_page
        }
    )


def calculate_recommendations(user_id):
    """计算用户的动画推荐"""
    # 获取用户的所有评分
    user_ratings = Rating.query.filter_by(user_id=user_id).all()

    if not user_ratings:
        return []

    # 存储每个标签的推荐度
    tag_recommendation = {}

    # 遍历用户评分的每个动画
    for rating in user_ratings:
        anime_id = rating.anime_id
        score = rating.score

        # 获取该动画的前10个标签及其数量
        top_tags = db.session.query(
            Tag.name,
            func.sum(AnimeTag.count).label('count')
        ).join(
            AnimeTag, Tag.id == AnimeTag.tag_id
        ).filter(
            AnimeTag.anime_id == anime_id
        ).group_by(
            Tag.name
        ).order_by(
            func.sum(AnimeTag.count).desc()
        ).limit(10).all()

        # 计算标签数量总和
        total_count = sum(tag.count for tag in top_tags)

        # 计算每个标签的推荐度
        for tag in top_tags:
            tag_name = tag.name
            tag_count = tag.count

            # 计算标签数量占比
            percentage = tag_count / total_count if total_count > 0 else 0

            # 根据评分确定基础推荐度
            if score <= 2:
                base_recommendation = -2
            elif score <= 2.5:
                base_recommendation = -1
            elif score <= 3.5:
                base_recommendation = 0
            elif score <= 4:
                base_recommendation = 1
            elif score <= 5:
                base_recommendation = 2
            else:
                base_recommendation = 0

            # 计算标签推荐度 = 基础推荐度 * 标签数量占比
            tag_recommendation_score = base_recommendation * percentage

            # 累加相同标签的推荐度
            if tag_name in tag_recommendation:
                tag_recommendation[tag_name] += tag_recommendation_score
            else:
                tag_recommendation[tag_name] = tag_recommendation_score

    # 获取所有动画
    all_animes = Anime.query.all()
    recommended_animes = []

    # 计算每个动画的总推荐度
    for anime in all_animes:
        # 跳过用户已经评分过的动画
        if Rating.query.filter_by(user_id=user_id, anime_id=anime.id).first():
            continue

        # 获取该动画的前10个标签及其数量
        top_tags = db.session.query(
            Tag.name,
            func.sum(AnimeTag.count).label('count')
        ).join(
            AnimeTag, Tag.id == AnimeTag.tag_id
        ).filter(
            AnimeTag.anime_id == anime.id
        ).group_by(
            Tag.name
        ).order_by(
            func.sum(AnimeTag.count).desc()
        ).limit(10).all()

        # 计算标签数量总和
        total_count = sum(tag.count for tag in top_tags)
        anime_recommendation = 0

        # 计算该动画的总推荐度
        for tag in top_tags:
            tag_name = tag.name
            tag_count = tag.count
            percentage = tag_count / total_count if total_count > 0 else 0

            # 获取标签推荐度，未评价过的标签默认为0
            tag_rec = tag_recommendation.get(tag_name, 0)
            anime_recommendation += tag_rec * percentage

        # 只推荐总推荐度大于0的动画
        if anime_recommendation > 0:
            recommended_animes.append((anime, anime_recommendation))

    # 按推荐度降序排序
    recommended_animes.sort(key=lambda x: x[1], reverse=True)

    return recommended_animes


# 账号管理页面
@app.route('/account')
def account():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user = db.session.get(User, user_id)
    return render_template('account.html', user=user)


# 修改评分页面
@app.route('/update_rating/<int:rating_id>', methods=['GET', 'POST'])
def update_rating(rating_id):
    if 'user_id' not in session:
        flash('请先登录', 'error')
        return redirect(url_for('login'))

    rating = Rating.query.filter_by(id=rating_id, user_id=session['user_id']).first()
    if not rating:
        flash('评分不存在', 'error')
        return redirect(url_for('mypage'))

    if request.method == 'POST':
        try:
            # 获取各维度评分
            animation = int(request.form.get('animation'))
            direction = int(request.form.get('direction'))
            voice_acting = int(request.form.get('voice_acting'))
            music = int(request.form.get('music'))
            story = int(request.form.get('story'))

            # 验证评分范围
            for score in [animation, direction, voice_acting, music, story]:
                if score < 1 or score > 5:
                    raise ValueError("评分必须在1-5之间")

            # 计算加权总分
            score = 0.4 * direction + 0.2 * animation + 0.2 * story + 0.1 * music + 0.1 * voice_acting

            # 更新评分
            rating.animation = animation
            rating.direction = direction
            rating.voice_acting = voice_acting
            rating.music = music
            rating.story = story
            rating.score = score

            db.session.commit()
            flash('评分已更新', 'success')
            return redirect(url_for('mypage'))

        except (ValueError, TypeError) as e:
            flash(f'评分数据无效: {str(e)}', 'error')

    return render_template('update_rating.html', rating=rating)


# 账号更新路由
@app.route('/update_account', methods=['POST'])
def update_account():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']
    user = db.session.get(User, user_id)

    if not user:
        return redirect(url_for('login'))

    action = request.form.get('action')

    if action == 'update_username':
        new_username = request.form['new_username']
        if User.query.filter_by(username=new_username).first() and new_username != user.username:
            return render_template('account.html', user=user, error='用户名已存在')
        user.username = new_username
        session['username'] = new_username

    elif action == 'update_password':
        old_password = request.form['old_password']
        new_password = request.form['new_password']

        if user.password != old_password:
            return render_template('account.html', user=user, error='旧密码错误')

        user.password = new_password

    db.session.commit()
    flash('账号信息已更新！')
    return redirect(url_for('account'))


# 标签云页面路由
@app.route('/tag_cloud')
def tag_cloud():
    # 查询所有标签及其关联的动画数量
    tags_with_count = db.session.query(
        Tag.name,
        func.count(AnimeTag.anime_id).label('anime_count')
    ).join(
        AnimeTag, Tag.id == AnimeTag.tag_id
    ).group_by(
        Tag.name
    ).order_by(
        func.count(AnimeTag.anime_id).desc()
    ).all()

    return render_template('tag_cloud.html', tags=tags_with_count)


# 按标签筛选动画路由
@app.route('/tag/<string:tag_name>')
def anime_by_tag(tag_name):
    tag = Tag.query.filter_by(name=tag_name).first_or_404()

    # 查询包含该标签的动画
    animes = db.session.query(Anime).join(
        AnimeTag, Anime.id == AnimeTag.anime_id
    ).join(
        Tag, AnimeTag.tag_id == Tag.id
    ).filter(
        Tag.name == tag_name
    ).all()

    return render_template('animes_by_tag.html', tag=tag, animes=animes)


# 添加评论
@app.route('/anime/<int:anime_id>/comment', methods=['POST'])
def add_comment(anime_id):
    if 'user_id' not in session:
        flash('请先登录', 'error')
        return redirect(url_for('login'))

    content = request.form.get('content')
    if not content:
        flash('评论内容不能为空', 'error')
        return redirect(url_for('anime_detail', anime_id=anime_id))

    new_comment = Comment(
        anime_id=anime_id,
        user_id=session['user_id'],
        content=content
    )

    db.session.add(new_comment)
    db.session.commit()

    flash('评论成功', 'success')
    return redirect(url_for('anime_detail', anime_id=anime_id))


# 添加回复
@app.route('/comment/<int:comment_id>/reply', methods=['POST'])
def add_reply(comment_id):
    if 'user_id' not in session:
        flash('请先登录', 'error')
        return redirect(url_for('login'))

    content = request.form.get('content')
    if not content:
        flash('回复内容不能为空', 'error')
        comment = Comment.query.get_or_404(comment_id)
        return redirect(url_for('anime_detail', anime_id=comment.anime_id))

    new_reply = Reply(
        comment_id=comment_id,
        user_id=session['user_id'],
        content=content
    )

    db.session.add(new_reply)
    db.session.commit()

    comment = Comment.query.get(comment_id)
    flash('回复成功', 'success')
    return redirect(url_for('anime_detail', anime_id=comment.anime_id))


# 删除评论
@app.route('/comment/<int:comment_id>/delete', methods=['POST'])
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)

    # 检查权限
    if comment.user_id != session.get('user_id'):
        flash('没有权限删除此评论', 'error')
        return redirect(url_for('anime_detail', anime_id=comment.anime_id))

    db.session.delete(comment)
    db.session.commit()

    flash('评论已删除', 'success')
    return redirect(request.referrer or url_for('my_comments'))


# 删除回复
@app.route('/reply/<int:reply_id>/delete', methods=['POST'])
def delete_reply(reply_id):
    reply = Reply.query.get_or_404(reply_id)

    # 检查权限
    if reply.user_id != session.get('user_id'):
        flash('没有权限删除此回复', 'error')
        return redirect(url_for('anime_detail', anime_id=reply.comment.anime_id))

    db.session.delete(reply)
    db.session.commit()

    flash('回复已删除', 'success')
    return redirect(request.referrer or url_for('my_comments'))


# 我的评论页面
@app.route('/my-comments')
def my_comments():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    user_id = session['user_id']

    # 获取我的评论
    my_comments = Comment.query.filter_by(user_id=user_id).order_by(Comment.created_at.desc()).all()

    # 获取我的回复
    my_replies = Reply.query.filter_by(user_id=user_id).order_by(Reply.created_at.desc()).all()

    # 获取别人对我的评论的回复
    my_comment_ids = [c.id for c in my_comments]
    replies_to_my_comments = Reply.query.filter(Reply.comment_id.in_(my_comment_ids)).filter(
        Reply.user_id != user_id).order_by(Reply.created_at.desc()).all()

    return render_template('my_comments.html',
                           my_comments=my_comments,
                           my_replies=my_replies,
                           replies_to_my_comments=replies_to_my_comments)


@app.route('/submit_comment', methods=['POST'])
def submit_comment():
    if 'user_id' not in session:
        flash('请先登录才能发表评论', 'error')
        return redirect(url_for('login'))

    anime_id = request.form.get('anime_id')
    content = request.form.get('content', '').strip()

    if not content:
        flash('评论内容不能为空', 'error')
        return redirect(url_for('anime_detail', anime_id=anime_id))

    # 创建新评论
    new_comment = Comment(
        anime_id=anime_id,
        user_id=session['user_id'],
        content=content
    )

    db.session.add(new_comment)
    db.session.commit()
    flash('评论发布成功', 'success')
    return redirect(url_for('anime_detail', anime_id=anime_id))


@app.route('/submit_reply', methods=['POST'])
def submit_reply():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '请先登录'})

    data = request.get_json()
    comment_id = data.get('comment_id')
    content = data.get('content', '').strip()

    if not comment_id or not content:
        return jsonify({'success': False, 'message': '回复内容不能为空'})

    # 验证评论是否存在
    comment = Comment.query.get(comment_id)
    if not comment:
        return jsonify({'success': False, 'message': '评论不存在'})

    try:
        # 创建新回复
        new_reply = Reply(
            comment_id=comment_id,
            user_id=session['user_id'],
            content=content,
            created_at=datetime.utcnow()
        )

        db.session.add(new_reply)
        db.session.commit()
        return jsonify({'success': True, 'message': '回复成功'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'message': f'提交失败: {str(e)}'})


@app.cli.command("init-db")
@with_appcontext
def init_db_command():
    """初始化数据库表"""
    print(f"正在初始化数据库: {app.config['SQLALCHEMY_DATABASE_URI']}")
    db.create_all()
    print("数据库表初始化完成")


if __name__ == '__main__':
    # 在应用上下文内运行应用
    with app.app_context():
        # 确保所有模型都已导入并注册
        db.create_all()
    app.run(debug=True)