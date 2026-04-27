#file name: app/app.py

import os
from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager
from fastapi import FastAPI, File, Form, UploadFile, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.middleware.cors import CORSMiddleware
from app.db import Post, create_db_and_tables, get_async_session, Comment
from fastapi import Header
from jose import jwt, JWTError
from app.auth import SECRET_KEY, ALGORITHM
from app.db import User, Friendship, Message, Report
from app.auth import hash_password, verify_password, create_token
import shutil
from sqlalchemy import or_
from sqlalchemy.orm import aliased
from datetime import datetime, timezone, timedelta
from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, List


BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000")
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()
    yield

app = FastAPI(title="Loopin API",lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 🔥 Serve uploaded files
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


@app.get("/")
async def root():
    return {"message": "FastAPI is running!", "endpoints": {
        "POST /signup": "Create a new user",
        "POST /login": "Login with username and password",
        "POST /upload": "Upload a file",
        "POST /upload-profile": "Upload profile picture",
        "GET /profile/{username}": "Get user profile",
        "GET /feed": "Get all posts",
        "POST /comment/{post_id}": "Add a comment to a post",
        "POST /like/{post_id}": "Like a post",
        "DELETE /post/{post_id}": "Delete a post",
        "GET /comments/{post_id}": "Get comments for a post"
    }}


@app.post("/upload-profile")
async def upload_profile(
    profile: UploadFile = File(...),
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    try:
        # Handle both "Bearer <token>" and plain token formats
        if " " in authorization:
            token = authorization.split(" ")[1]
        else:
            token = authorization
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
    except (IndexError, JWTError) as e:
        raise HTTPException(status_code=401, detail="Invalid token")

    try:
        # Save profile picture
        safe_filename = f"profile_{username}_{profile.filename.replace(' ', '_')}"
        file_path = os.path.join(UPLOAD_DIR, safe_filename)

        content = await profile.read()
        with open(file_path, "wb") as f:
            f.write(content)

        file_url = f"{BASE_URL}/uploads/{safe_filename}"

        # Update user profile picture
        result = await session.execute(
            select(User).where(User.username == username)
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        user.profile_pic = file_url
        await session.commit()

        return {"message": "Profile picture updated", "profile_pic": file_url}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@app.get("/profile/{username}")
async def get_profile(username: str, session: AsyncSession = Depends(get_async_session)):
    result = await session.execute(
        select(User).where(User.username == username)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    return {
        "username": user.username,
        "profile_pic": user.profile_pic
    }


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    caption: str = Form(""),
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    try:
        # 🔴 1. Check login
        if not authorization:
            raise HTTPException(status_code=401, detail="Login required")

        try:
            # Handle both "Bearer <token>" and plain token formats
            if " " in authorization:
                token = authorization.split(" ")[1]
            else:
                token = authorization
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            await check_frozen(username, session)
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid token")

        # 🔴 2. Save file
        safe_filename = file.filename.replace(" ", "_")
        file_path = os.path.join(UPLOAD_DIR, safe_filename)

        with open(file_path, "wb") as f:
            shutil.copyfileobj(file.file, f)

        file_url = f"{BASE_URL}/uploads/{safe_filename}"

        # 🔴 3. Save post WITH USER
        post = Post(
            caption=caption,
            url=file_url,
            filetype="image",
            filename=safe_filename,
            # 👇 add this field in DB (next step)
            username=username
        )

        session.add(post)
        await session.commit()
        await session.refresh(post)

        return post

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/like/{post_id}")
async def like_post(post_id: str, session: AsyncSession = Depends(get_async_session)):
    result = await session.execute(
        select(Post).where(Post.id == post_id)
    )
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    post.likes += 1

    await session.commit()
    await session.refresh(post)

    return {"likes": post.likes}




@app.post("/comment/{post_id}")
async def add_comment(
    post_id: str,
    text: str = Form(...),
    authorization: str = Header(None),        # ✅ added
    session: AsyncSession = Depends(get_async_session)
):
    username = None
    if authorization:
        try:
            token = authorization.split(" ")[1] if " " in authorization else authorization
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            await check_frozen(username, session)
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        raise HTTPException(status_code=401, detail="Login required")  # ✅ auth enforced

    comment = Comment(text=text, post_id=post_id, username=username)  # ✅ store username
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    return {"message": "comment added"}


@app.post("/signup")
async def signup(username: str = Form(...), password: str = Form(...), session: AsyncSession = Depends(get_async_session)):
    # Check if username already exists
    result = await session.execute(
        select(User).where(User.username == username)
    )
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    # Check if this is the admin user
    is_admin = (username == "admin" and password == "admin875")
    
    user = User(
        username=username,
        password=hash_password(password),
        is_admin=is_admin
    )

    session.add(user)
    await session.commit()

    return {"message": "User created", "is_admin": is_admin}



@app.get("/all-users")
async def get_all_users(
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    # Check if user is admin
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    try:
        # Handle both "Bearer <token>" and plain token formats
        if " " in authorization:
            token = authorization.split(" ")[1]
        else:
            token = authorization
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
    except (IndexError, JWTError):
        raise HTTPException(status_code=401, detail="Invalid token")

    # Check if user is admin
    result = await session.execute(
        select(User).where(User.username == username)
    )
    user = result.scalar_one_or_none()

    if not user or not user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Get all users
    all_users_result = await session.execute(select(User))
    all_users = all_users_result.scalars().all()

    return {
        "users": [
            {
                "username": u.username,
                "is_admin": u.is_admin,
                "is_frozen": u.is_frozen
            }
            for u in all_users
        ]
    }


@app.post("/update-user-password")
async def update_user_password(
    target_username: str = Form(...),
    new_password: str = Form(...),
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    # Check if user is admin
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    try:
        # Handle both "Bearer <token>" and plain token formats
        if " " in authorization:
            token = authorization.split(" ")[1]
        else:
            token = authorization
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        admin_username = payload.get("sub")
    except (IndexError, JWTError):
        raise HTTPException(status_code=401, detail="Invalid token")

    # Check if user is admin
    result = await session.execute(
        select(User).where(User.username == admin_username)
    )
    admin_user = result.scalar_one_or_none()

    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Update target user's password
    target_result = await session.execute(
        select(User).where(User.username == target_username)
    )
    target_user = target_result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    target_user.password = hash_password(new_password)
    await session.commit()

    return {"message": f"Password updated for {target_username}"}


@app.post("/login")
async def login(username: str = Form(...), password: str = Form(...), session: AsyncSession = Depends(get_async_session)):
    result = await session.execute(
        select(User).where(User.username == username)
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(password, user.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_token({"sub": user.username})

    return {"access_token": token, "is_admin": user.is_admin}


@app.delete("/post/{post_id}")
async def delete_post(
    post_id: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    result = await session.execute(
        select(Post).where(Post.id == post_id)
    )
    post = result.scalar_one_or_none()

    if not post:
        raise HTTPException(status_code=404, detail="Post not found")

    if authorization:
        try:
            token = authorization.split(" ")[1] if " " in authorization else authorization
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
        except (IndexError, JWTError):
            raise HTTPException(status_code=401, detail="Invalid token")
    else:
        raise HTTPException(status_code=401, detail="Login required")

    user_result = await session.execute(
        select(User).where(User.username == username)
    )
    user = user_result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if not user.is_admin and post.username != username:
        raise HTTPException(status_code=403, detail="Can only delete your own posts")

    # ✅ Delete comments first before deleting post
    comments_result = await session.execute(
        select(Comment).where(Comment.post_id == post_id)
    )
    comments = comments_result.scalars().all()
    for comment in comments:
        await session.delete(comment)

    # 🗑 delete image file
    file_path = os.path.join("uploads", post.filename)
    if os.path.exists(file_path):
        os.remove(file_path)

    await session.delete(post)
    await session.commit()

    return {"message": "Post deleted"}



@app.get("/feed")
async def get_feed(
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    try:
        if not authorization:
            raise HTTPException(status_code=401, detail="Login required")

        try:
            token = authorization.split(" ")[1] if " " in authorization else authorization
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            current_username = payload.get("sub")
            if not current_username:
                raise HTTPException(status_code=401, detail="Invalid token: no username")
        except (IndexError, JWTError) as e:
            raise HTTPException(status_code=401, detail=f"Invalid token: {str(e)}")

        user_result = await session.execute(
            select(User).where(User.username == current_username)
        )
        current_user = user_result.scalar_one_or_none()

        if not current_user:
            raise HTTPException(status_code=404, detail="User not found")

        PostAuthor = aliased(User)

        if current_user.is_admin:
            result = await session.execute(
                select(Post, PostAuthor)
                .join(PostAuthor, Post.username == PostAuthor.username)
                .order_by(Post.created_at.desc())
            )
        else:
            result = await session.execute(
                select(Post, PostAuthor)
                .join(PostAuthor, Post.username == PostAuthor.username)
                .where(
                    or_(
                        Post.username == current_username,
                        PostAuthor.is_admin == True
                    )
                )
                .order_by(Post.created_at.desc())
            )

        posts_with_users = result.all()  # ✅ this line was missing

        return {
            "is_frozen": current_user.is_frozen,
            "posts": [
                {
                    "id": str(post.id),
                    "caption": post.caption,
                    "url": post.url,
                    "filetype": post.filetype,
                    "filename": post.filename,
                    "created_at": post.created_at.isoformat(),
                    "likes": post.likes,
                    "username": post.username,
                    "profile_pic": user.profile_pic,
                    "is_admin_post": user.is_admin
                }
                for post, user in posts_with_users  # ✅ now defined
            ]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")



@app.get("/comments/{post_id}")
async def get_comments(post_id: str, session: AsyncSession = Depends(get_async_session)):
    result = await session.execute(
        select(Comment).where(Comment.post_id == post_id).order_by(Comment.created_at.desc())
    )
    comments = result.scalars().all()
    return [
        {
            "text": c.text,
            "username": c.username or "user",  # ✅ return actual username
            "created_at": c.created_at.isoformat()
        }
        for c in comments
    ]

@app.get("/search/{username}")
async def search_user(
    username: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    result = await session.execute(
        select(User).where(
            User.username.ilike(f"%{username}%"),
            User.username != current_username  # don't show yourself
        )
    )
    users = result.scalars().all()

    # For each user, check friendship status
    search_results = []
    for u in users:
        friendship = await session.execute(
            select(Friendship).where(
                or_(
                    (Friendship.sender == current_username) & (Friendship.receiver == u.username),
                    (Friendship.sender == u.username) & (Friendship.receiver == current_username)
                )
            )
        )
        f = friendship.scalar_one_or_none()
        status = f.status if f else None
        is_sender = f.sender == current_username if f else False

        search_results.append({
            "username": u.username,
            "profile_pic": u.profile_pic,
            "friendship_status": status,       # None / "pending" / "accepted"
            "is_sender": is_sender
        })

    return {"users": search_results}

# Send friend request
@app.post("/friend-request/{receiver}")
async def send_friend_request(
    receiver: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    sender = payload.get("sub")

    # Check if request already exists
    existing = await session.execute(
        select(Friendship).where(
            or_(
                (Friendship.sender == sender) & (Friendship.receiver == receiver),
                (Friendship.sender == receiver) & (Friendship.receiver == sender)
            )
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Friend request already exists")

    friendship = Friendship(sender=sender, receiver=receiver, status="pending")
    session.add(friendship)
    await session.commit()
    return {"message": "Friend request sent"}


# Accept friend request
@app.post("/accept-friend/{sender}")
async def accept_friend_request(
    sender: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    receiver = payload.get("sub")

    result = await session.execute(
        select(Friendship).where(
            Friendship.sender == sender,
            Friendship.receiver == receiver,
            Friendship.status == "pending"
        )
    )
    friendship = result.scalar_one_or_none()
    if not friendship:
        raise HTTPException(status_code=404, detail="Friend request not found")

    friendship.status = "accepted"
    await session.commit()
    return {"message": "Friend request accepted"}


# Get friends and pending requests
@app.get("/friends")
async def get_friends(
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    # Get all friendships involving current user
    result = await session.execute(
        select(Friendship).where(
            or_(
                Friendship.sender == current_username,
                Friendship.receiver == current_username
            )
        )
    )
    friendships = result.scalars().all()

    friends = []
    pending_received = []   # requests others sent to me
    pending_sent = []       # requests I sent

    for f in friendships:
        other = f.receiver if f.sender == current_username else f.sender
        # get profile pic
        user_res = await session.execute(select(User).where(User.username == other))
        user = user_res.scalar_one_or_none()
        pic = user.profile_pic if user else None

        entry = {"username": other, "profile_pic": pic, "friendship_id": str(f.id)}

        if f.status == "accepted":
            friends.append(entry)
        elif f.status == "pending" and f.receiver == current_username:
            pending_received.append(entry)
        elif f.status == "pending" and f.sender == current_username:
            pending_sent.append(entry)

    return {
        "friends": friends,
        "pending_received": pending_received,
        "pending_sent": pending_sent
    }

@app.get("/friends/{username}")
async def get_user_friends(
    username: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    # Check if requester is admin
    admin_result = await session.execute(
        select(User).where(User.username == current_username)
    )
    admin_user = admin_result.scalar_one_or_none()

    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Get all friendships of target user
    result = await session.execute(
        select(Friendship).where(
            or_(
                Friendship.sender == username,
                Friendship.receiver == username
            )
        )
    )
    friendships = result.scalars().all()

    friends = []
    pending_received = []
    pending_sent = []

    for f in friendships:
        other = f.receiver if f.sender == username else f.sender
        user_res = await session.execute(select(User).where(User.username == other))
        user = user_res.scalar_one_or_none()
        pic = user.profile_pic if user else None
        entry = {"username": other, "profile_pic": pic}

        if f.status == "accepted":
            friends.append(entry)
        elif f.status == "pending" and f.receiver == username:
            pending_received.append(entry)
        else:
            pending_sent.append(entry)

    return {
        "friends": friends,
        "pending_received": pending_received,
        "pending_sent": pending_sent
    }

@app.delete("/friend/{username}")
async def remove_friend(
    username: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    # Find the friendship
    result = await session.execute(
        select(Friendship).where(
            or_(
                (Friendship.sender == current_username) & (Friendship.receiver == username),
                (Friendship.sender == username) & (Friendship.receiver == current_username)
            )
        )
    )
    friendship = result.scalar_one_or_none()

    if not friendship:
        raise HTTPException(status_code=404, detail="Friendship not found")

    await session.delete(friendship)
    await session.commit()
    return {"message": f"Removed {username} from friends"}

@app.delete("/user/{username}")
async def delete_user(
    username: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    # Check if requester is admin
    admin_result = await session.execute(
        select(User).where(User.username == current_username)
    )
    admin_user = admin_result.scalar_one_or_none()

    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Prevent admin from deleting themselves
    if username == current_username:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    # Find target user
    target_result = await session.execute(
        select(User).where(User.username == username)
    )
    target_user = target_result.scalar_one_or_none()

    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    # ✅ Delete comments on user's posts FIRST (foreign key constraint)
    posts_result = await session.execute(
        select(Post).where(Post.username == username)
    )
    posts = posts_result.scalars().all()
    for post in posts:
        comments_on_post = await session.execute(
            select(Comment).where(Comment.post_id == post.id)
        )
        for comment in comments_on_post.scalars().all():
            await session.delete(comment)

    # ✅ Delete all comments made by user on other posts
    comments_result = await session.execute(
        select(Comment).where(Comment.username == username)
    )
    for comment in comments_result.scalars().all():
        await session.delete(comment)

    # Now safe to delete posts
    for post in posts:
        file_path = os.path.join("uploads", post.filename)
        if os.path.exists(file_path):
            os.remove(file_path)
        await session.delete(post)
   

    # Delete all their friendships
    friendships_result = await session.execute(
        select(Friendship).where(
            or_(
                Friendship.sender == username,
                Friendship.receiver == username
            )
        )
    )
    friendships = friendships_result.scalars().all()
    for friendship in friendships:
        await session.delete(friendship)

    # Delete all their messages
    messages_result = await session.execute(
        select(Message).where(
            or_(
                Message.sender == username,
                Message.receiver == username
            )
        )
    )
    messages = messages_result.scalars().all()
    for message in messages:
        await session.delete(message)

    # Delete all their reports
    reports_result = await session.execute(
        select(Report).where(
            or_(
                Report.reporter == username,
                Report.reported == username
            )
        )
    )
    reports = reports_result.scalars().all()
    for report in reports:
        await session.delete(report)
        
    # Delete profile picture file if exists
    if target_user.profile_pic:
        pic_filename = target_user.profile_pic.split("/uploads/")[-1]
        pic_path = os.path.join("uploads", pic_filename)
        if os.path.exists(pic_path):
            os.remove(pic_path)

    # Finally delete the user
    await session.delete(target_user)
    await session.commit()

    return {"message": f"User {username} deleted successfully"}



@app.post("/message/{receiver}")
async def send_message(
    receiver: str,
    text: str = Form(...),
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    sender = payload.get("sub")
    await check_frozen(sender, session)

    receiver_result = await session.execute(select(User).where(User.username == receiver))
    receiver_user = receiver_result.scalar_one_or_none()
    if not receiver_user:
        raise HTTPException(status_code=404, detail="User not found")

    if not receiver_user.is_admin:
        friendship_result = await session.execute(
            select(Friendship).where(
                or_(
                    (Friendship.sender == sender) & (Friendship.receiver == receiver),
                    (Friendship.sender == receiver) & (Friendship.receiver == sender)
                ),
                Friendship.status == "accepted"
            )
        )
        if not friendship_result.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="You can only message friends")

    message = Message(sender=sender, receiver=receiver, text=text)
    session.add(message)
    await session.commit()

    # ✅ Real time notification
    await manager.send_personal_message({
        "type": "new_message",
        "sender": sender,
        "text": text,
        "created_at": message.created_at.isoformat()
    }, receiver)

    return {"message": "Message sent"}


@app.get("/messages/{username}")
async def get_messages(
    username: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    # Check if they are friends
    # Check if admin or friends
    admin_check = await session.execute(select(User).where(User.username == current_username))
    current_user = admin_check.scalar_one_or_none()

    other_user_check = await session.execute(select(User).where(User.username == username))
    other_user = other_user_check.scalar_one_or_none()

    # Allow if either party is admin, otherwise check friendship
    if not (current_user and current_user.is_admin) and not (other_user and other_user.is_admin):
        friendship_result = await session.execute(
            select(Friendship).where(
                or_(
                    (Friendship.sender == current_username) & (Friendship.receiver == username),
                    (Friendship.sender == username) & (Friendship.receiver == current_username)
                ),
                Friendship.status == "accepted"
            )
        )
        if not friendship_result.scalar_one_or_none():
            raise HTTPException(status_code=403, detail="You can only view messages with friends")

    result = await session.execute(
        select(Message).where(
            or_(
                (Message.sender == current_username) & (Message.receiver == username),
                (Message.sender == username) & (Message.receiver == current_username)
            )
        ).order_by(Message.created_at.asc())
    )
    messages = result.scalars().all()

    return [
        {
            "sender": m.sender,
            "receiver": m.receiver,
            "text": m.text,
            "created_at": m.created_at.isoformat()
        }
        for m in messages
    ]

@app.delete("/messages/{username}")
async def delete_chat_history(
    username: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    # Check if they are friends
    friendship_result = await session.execute(
        select(Friendship).where(
            or_(
                (Friendship.sender == current_username) & (Friendship.receiver == username),
                (Friendship.sender == username) & (Friendship.receiver == current_username)
            ),
            Friendship.status == "accepted"
        )
    )
    if not friendship_result.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="You can only delete messages with friends")

    # ✅ Check if either user has a pending report against the other
    report_result = await session.execute(
        select(Report).where(
            or_(
                (Report.reporter == current_username) & (Report.reported == username),
                (Report.reporter == username) & (Report.reported == current_username)
            ),
            Report.is_reviewed == False  # only block if pending
        )
    )
    if report_result.scalar_one_or_none():
        raise HTTPException(
            status_code=403,
            detail="Chat cannot be cleared!"
        )

    # Delete all messages between the two users
    result = await session.execute(
        select(Message).where(
            or_(
                (Message.sender == current_username) & (Message.receiver == username),
                (Message.sender == username) & (Message.receiver == current_username)
            )
        )
    )
    messages = result.scalars().all()
    for message in messages:
        await session.delete(message)

    await session.commit()
    return {"message": "Chat history deleted"}

@app.post("/report/{username}")
async def report_user(
    username: str,
    reason: str = Form(""),
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    if current_username == username:
        raise HTTPException(status_code=400, detail="Cannot report yourself")

    # Check if already reported and not yet reviewed
    existing = await session.execute(
        select(Report).where(
            Report.reporter == current_username,
            Report.reported == username,
            Report.is_reviewed == False  
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="You have already reported this user, wait for admin to review")

    report = Report(reporter=current_username, reported=username, reason=reason)
    session.add(report)
    await session.commit()
    return {"message": f"User {username} reported successfully"}


@app.get("/admin/reports")
async def get_reports(
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    # Check admin
    admin_result = await session.execute(
        select(User).where(User.username == current_username)
    )
    admin_user = admin_result.scalar_one_or_none()
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await session.execute(
        select(Report).where(Report.is_reviewed == False).order_by(Report.created_at.desc())
    )
    reports = result.scalars().all()

    report_list = []
    for r in reports:
        # Get message history between reporter and reported
        messages_result = await session.execute(
            select(Message).where(
                or_(
                    (Message.sender == r.reporter) & (Message.receiver == r.reported),
                    (Message.sender == r.reported) & (Message.receiver == r.reporter)
                )
            ).order_by(Message.created_at.asc())
        )
        messages = messages_result.scalars().all()

        report_list.append({
            "id": str(r.id),
            "reporter": r.reporter,
            "reported": r.reported,
            "reason": r.reason,
            "created_at": r.created_at.isoformat(),
            "messages": [
                {
                    "sender": m.sender,
                    "text": m.text,
                    "created_at": m.created_at.isoformat()
                }
                for m in messages
            ]
        })

    return {"reports": report_list, "count": len(report_list)}


@app.post("/admin/report/{report_id}/reviewed")
async def mark_report_reviewed(
    report_id: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    admin_result = await session.execute(
        select(User).where(User.username == current_username)
    )
    admin_user = admin_result.scalar_one_or_none()
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await session.execute(
        select(Report).where(Report.id == report_id)
    )
    report = result.scalar_one_or_none()
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    report.is_reviewed = True
    await session.commit()
    return {"message": "Report marked as reviewed"}


# -------- helper to check if user is frozen --------
async def check_frozen(username: str, session: AsyncSession):
    result = await session.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        return
    # Auto-unfreeze if time has passed
    if user.is_frozen and user.frozen_until:
        if datetime.now(timezone.utc) > user.frozen_until.replace(tzinfo=timezone.utc):
            user.is_frozen = False
            user.frozen_until = None
            await session.commit()
            return
    if user.is_frozen:
        until = user.frozen_until.strftime("%Y-%m-%d %H:%M") if user.frozen_until else "indefinitely"
        raise HTTPException(status_code=403, detail=f"Your account is frozen until {until} UTC")


# -------- Freeze user --------
@app.post("/admin/freeze/{username}")
async def freeze_user(
    username: str,
    hours: int = Form(...),
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    admin_result = await session.execute(select(User).where(User.username == current_username))
    admin_user = admin_result.scalar_one_or_none()
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    target_result = await session.execute(select(User).where(User.username == username))
    target_user = target_result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    from datetime import timedelta
    target_user.is_frozen = True
    target_user.frozen_until = datetime.now(timezone.utc) + timedelta(hours=hours)
    await session.commit()

    return {"message": f"User {username} frozen for {hours} hours"}


# -------- Unfreeze user --------
@app.post("/admin/unfreeze/{username}")
async def unfreeze_user(
    username: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    admin_result = await session.execute(select(User).where(User.username == current_username))
    admin_user = admin_result.scalar_one_or_none()
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    target_result = await session.execute(select(User).where(User.username == username))
    target_user = target_result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    target_user.is_frozen = False
    target_user.frozen_until = None
    await session.commit()

    return {"message": f"User {username} unfrozen"}


# -------- Admin message any user --------
@app.post("/admin/message/{receiver}")
async def admin_send_message(
    receiver: str,
    text: str = Form(...),
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    admin_result = await session.execute(select(User).where(User.username == current_username))
    admin_user = admin_result.scalar_one_or_none()
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    # Check receiver exists
    receiver_result = await session.execute(select(User).where(User.username == receiver))
    if not receiver_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="User not found")

    message = Message(sender=current_username, receiver=receiver, text=text)
    session.add(message)
    await session.commit()
    # ✅ Notify receiver in real time if online
    await manager.send_personal_message({
        "type": "new_message",
        "sender": current_username,  # or current_username for admin
        "text": text,
        "created_at": message.created_at.isoformat()
    }, receiver)
    return {"message": "Message sent"}


# -------- Get admin messages with a user --------
@app.get("/admin/messages/{username}")
async def admin_get_messages(
    username: str,
    authorization: str = Header(None),
    session: AsyncSession = Depends(get_async_session)
):
    if not authorization:
        raise HTTPException(status_code=401, detail="Login required")

    token = authorization.split(" ")[1] if " " in authorization else authorization
    payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    current_username = payload.get("sub")

    admin_result = await session.execute(select(User).where(User.username == current_username))
    admin_user = admin_result.scalar_one_or_none()
    if not admin_user or not admin_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    result = await session.execute(
        select(Message).where(
            or_(
                (Message.sender == current_username) & (Message.receiver == username),
                (Message.sender == username) & (Message.receiver == current_username)
            )
        ).order_by(Message.created_at.asc())
    )
    messages = result.scalars().all()
    return [
        {
            "sender": m.sender,
            "text": m.text,
            "created_at": m.created_at.isoformat()
        }
        for m in messages
    ]

# -------- Connection Manager --------
class ConnectionManager:
    def __init__(self):
        # Store active connections: {username: websocket}
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, username: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[username] = websocket

    def disconnect(self, username: str):
        if username in self.active_connections:
            del self.active_connections[username]

    async def send_personal_message(self, message: dict, username: str):
        if username in self.active_connections:
            await self.active_connections[username].send_json(message)

manager = ConnectionManager()


@app.websocket("/ws/{username}")
async def websocket_endpoint(
    websocket: WebSocket,
    username: str,
    token: str,
    session: AsyncSession = Depends(get_async_session)
):
    # Verify token
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        token_username = payload.get("sub")
        if token_username != username:
            await websocket.close(code=1008)
            return
    except JWTError:
        await websocket.close(code=1008)
        return

    await manager.connect(username, websocket)
    try:
        while True:
            # Keep connection alive — actual messages sent via REST
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(username)