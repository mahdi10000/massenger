from flask import Flask, render_template, request, redirect, url_for, session
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import uuid
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'alphamessenger-secret-2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///messenger.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ===== مدل‌ها =====

class User(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    username = db.Column(db.String(50), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    is_online = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Message(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    content = db.Column(db.Text, nullable=False)
    sender_id = db.Column(db.String(36), db.ForeignKey('user.id'))
    receiver_id = db.Column(db.String(36), nullable=True)
    group_id = db.Column(db.String(36), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sender = db.relationship('User', foreign_keys=[sender_id])

class Group(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), nullable=False)
    is_channel = db.Column(db.Boolean, default=False)
    owner_id = db.Column(db.String(36), db.ForeignKey('user.id'))

class GroupMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.String(36), db.ForeignKey('group.id'))
    user_id = db.Column(db.String(36), db.ForeignKey('user.id'))
    is_admin = db.Column(db.Boolean, default=False)

# ===== روت‌ها =====

@app.route('/')
def index():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    return render_template('chat.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    error = None
    if request.method == 'POST':
        username = request.form['username'].strip()
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            error = 'این نام کاربری قبلاً گرفته شده'
        else:
            user = User(username=username,
                       password=generate_password_hash(password))
            db.session.add(user)
            db.session.commit()
            return redirect(url_for('login'))
    return render_template('register.html', error=error)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password, request.form['password']):
            session['user_id'] = user.id
            session['username'] = user.username
            return redirect(url_for('index'))
        error = 'نام کاربری یا رمز اشتباه است'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            user.is_online = False
            db.session.commit()
    session.clear()
    return redirect(url_for('login'))

# ===== API =====

@app.route('/api/contacts')
def get_contacts():
    if 'user_id' not in session:
        return {}, 401
    users = User.query.filter(User.id != session['user_id']).all()
    return {'contacts': [{'id': u.id, 'username': u.username,
                          'online': u.is_online} for u in users]}

@app.route('/api/groups')
def get_groups():
    if 'user_id' not in session:
        return {}, 401
    memberships = GroupMember.query.filter_by(user_id=session['user_id']).all()
    group_ids = [m.group_id for m in memberships]
    groups = Group.query.filter(Group.id.in_(group_ids)).all()
    return {'groups': [{'id': g.id, 'name': g.name,
                        'is_channel': g.is_channel} for g in groups]}

@app.route('/api/messages/<chat_type>/<chat_id>')
def get_messages(chat_type, chat_id):
    if 'user_id' not in session:
        return {}, 401
    if chat_type == 'private':
        msgs = Message.query.filter(
            ((Message.sender_id == session['user_id']) & (Message.receiver_id == chat_id)) |
            ((Message.sender_id == chat_id) & (Message.receiver_id == session['user_id']))
        ).order_by(Message.created_at).limit(50).all()
    else:
        msgs = Message.query.filter_by(group_id=chat_id)\
                            .order_by(Message.created_at).limit(50).all()
    return {'messages': [{
        'content': m.content,
        'sender': m.sender.username,
        'sender_id': m.sender_id,
        'time': m.created_at.strftime('%H:%M'),
    } for m in msgs]}

@app.route('/api/create_group', methods=['POST'])
def create_group():
    if 'user_id' not in session:
        return {}, 401
    data = request.json
    group = Group(name=data['name'],
                  is_channel=data.get('is_channel', False),
                  owner_id=session['user_id'])
    db.session.add(group)
    db.session.flush()
    db.session.add(GroupMember(group_id=group.id,
                               user_id=session['user_id'], is_admin=True))
    db.session.commit()
    return {'id': group.id, 'name': group.name}

@app.route('/api/join_group/<group_id>', methods=['POST'])
def join_group(group_id):
    if 'user_id' not in session:
        return {}, 401
    exists = GroupMember.query.filter_by(
        group_id=group_id, user_id=session['user_id']).first()
    if not exists:
        db.session.add(GroupMember(group_id=group_id,
                                   user_id=session['user_id']))
        db.session.commit()
    return {'ok': True}

# ===== WebSocket =====

@socketio.on('connect')
def on_connect():
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            user.is_online = True
            db.session.commit()
            join_room(session['user_id'])
            memberships = GroupMember.query.filter_by(
                user_id=session['user_id']).all()
            for m in memberships:
                join_room(m.group_id)
            emit('user_online', {'user_id': session['user_id']}, broadcast=True)

@socketio.on('disconnect')
def on_disconnect():
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
        if user:
            user.is_online = False
            db.session.commit()
            emit('user_offline', {'user_id': session['user_id']}, broadcast=True)

@socketio.on('send_message')
def handle_message(data):
    if 'user_id' not in session:
        return
    msg = Message(
        content=data['content'],
        sender_id=session['user_id'],
        receiver_id=data.get('receiver_id'),
        group_id=data.get('group_id')
    )
    db.session.add(msg)
    db.session.commit()
    payload = {
        'content': msg.content,
        'sender': session['username'],
        'sender_id': session['user_id'],
        'time': msg.created_at.strftime('%H:%M'),
    }
    if data.get('group_id'):
        emit('new_message', payload, room=data['group_id'])
    else:
        emit('new_message', payload, room=data['receiver_id'])
        emit('new_message', payload, room=session['user_id'])

@socketio.on('typing')
def handle_typing(data):
    emit('typing', {'from': session['username']}, room=data['to'])

@socketio.on('call_signal')
def handle_call(data):
    emit('call_signal', {
        'from': session['user_id'],
        'from_name': session['username'],
        'signal_type': data['signal_type'],
        'data': data.get('data'),
        'call_type': data.get('call_type', 'video')
    }, room=data['to'])

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host='0.0.0.0', port=port, 
                 debug=False, allow_unsafe_werkzeug=True)
