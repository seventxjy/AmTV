from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

# 创建未绑定的SQLAlchemy实例
db = SQLAlchemy()


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)


class Anime(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, unique=True)
    name = db.Column(db.String(255), nullable=False)
    image_url = db.Column(db.String(255))
    url = db.Column(db.String(255))

    # 定义与Rating的关系 - 反向引用为'anime'
    ratings = db.relationship('Rating', backref='anime', lazy=True)

    # 与Tag的多对多关系，通过anime_tag关联表
    tags = db.relationship('Tag', secondary='anime_tag', backref='animes', lazy=True)


class Rating(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    anime_id = db.Column(db.Integer, db.ForeignKey('anime.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    # 新增五个评分维度
    animation = db.Column(db.Integer, nullable=False)  # 作画
    direction = db.Column(db.Integer, nullable=False)  # 演出
    voice_acting = db.Column(db.Integer, nullable=False)  # 配音
    music = db.Column(db.Integer, nullable=False)  # 音乐
    story = db.Column(db.Integer, nullable=False)  # 剧情

    # 将score改为浮点数
    score = db.Column(db.Float, nullable=False)  # 加权总分

    # 与User的关系，反向引用为'ratings'
    user = db.relationship('User', backref=db.backref('ratings', lazy=True))


class AnimeTag(db.Model):
    __tablename__ = 'anime_tag'
    anime_id = db.Column(db.Integer, db.ForeignKey('anime.id'), primary_key=True)
    tag_id = db.Column(db.Integer, db.ForeignKey('tag.id'), primary_key=True)
    count = db.Column(db.Integer, default=0)


class Tag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)


class Image(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    url = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    prompt = db.Column(db.Text, nullable=False)
    negative_prompt = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref='images')  # 建立与用户的关系

    def __repr__(self):
        return f'<Image {self.filename}>'


# 在models.py中添加以下内容
class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    anime_id = db.Column(db.Integer, db.ForeignKey('anime.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 关联用户和动画
    user = db.relationship('User', backref=db.backref('comments', lazy=True))
    anime = db.relationship('Anime', backref=db.backref('comments', lazy=True))

    # 关联回复
    replies = db.relationship('Reply', backref='comment', lazy=True, cascade="all, delete-orphan")


class Reply(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comment.id', ondelete='CASCADE'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # 关联用户
    user = db.relationship('User', backref=db.backref('replies', lazy=True))