import discord
from discord.ext import commands
from discord import app_commands
import random, string
from PIL import Image, ImageDraw, ImageFont
import io
import asyncio
from datetime import datetime, timedelta

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

captcha_codes = {}
auth_channels = {}
auth_timeouts = {}  # 길드별 인증 대기 시간(초) 저장
auth_punishments = {}  # 길드별 인증 실패 처벌 방식 저장
auth_fail_punishments = {}  # 길드별 인증 오답(틀림) 처벌 방식 저장
bot_use_channels = {}  # 길드별 봇 사용 채널 저장
user_up_cooldowns = {}  # 사용자별 up 쿨다운 저장
user_bump_cooldowns = {}  # 사용자별 bump 쿨다운 저장
warnings = {}  # 길드별 사용자 경고 저장 {guild_id: {user_id: [경고목록]}}
warn_limits = {}  # 길드별 경고 한계 설정 {guild_id: {"추방": 3, "차단": 5}}

def generate_captcha():
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    img = Image.new('RGB', (180, 70), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    # 글자
    draw.text((20, 20), code, font=font, fill=(0, 0, 0))

    # 방해선 추가
    for _ in range(8):
        x1 = random.randint(0, 180)
        y1 = random.randint(0, 70)
        x2 = random.randint(0, 180)
        y2 = random.randint(0, 70)
        draw.line(((x1, y1), (x2, y2)), fill=(random.randint(0,255), random.randint(0,255), random.randint(0,255)), width=2)

    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return code, buffer

@bot.event
async def on_ready():
    print(f"✅ 봇 시작됨: {bot.user}")
    try:
        synced = await tree.sync()
        print(f"🌐 {len(synced)}개의 slash 명령 동기화됨")
    except Exception as e:
        print(f"명령 동기화 실패: {e}")

@tree.command(name="인증채널설정", description="인증 메시지를 보낼 채널을 설정합니다.")
@app_commands.describe(channel="인증에 사용할 채널")
async def set_auth_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    auth_channels[interaction.guild.id] = channel.id
    await interaction.response.send_message(f"✅ 인증 채널이 {channel.mention} 으로 설정되었습니다.", ephemeral=True)

@tree.command(name="인증초설정", description="인증 대기 시간을 초 단위로 설정합니다.")
@app_commands.describe(seconds="인증 대기 시간(초)")
async def set_auth_timeout(interaction: discord.Interaction, seconds: int):
    if seconds < 10 or seconds > 600:
        await interaction.response.send_message("⏱️ 10초 이상 600초 이하로 입력해주세요.", ephemeral=True)
        return
    auth_timeouts[interaction.guild.id] = seconds
    await interaction.response.send_message(f"⏱️ 인증 대기 시간이 {seconds}초로 설정되었습니다.", ephemeral=True)

@tree.command(name="인증처벌", description="인증 실패 시 처벌 방식을 설정합니다.")
@app_commands.describe(punishment="인증 실패 시 적용할 처벌을 선택하세요.")
@app_commands.choices(
    punishment=[
        app_commands.Choice(name="추방", value="추방"),
        app_commands.Choice(name="차단", value="차단"),
        app_commands.Choice(name="역할 경고", value="역할 경고"),
        app_commands.Choice(name="다시 시도하게", value="다시 시도하게"),
    ]
)
async def set_auth_punishment(interaction: discord.Interaction, punishment: app_commands.Choice[str]):
    auth_punishments[interaction.guild.id] = punishment.value
    await interaction.response.send_message(f"⚠️ 인증 실패 시 '{punishment.value}' 처벌이 적용됩니다.", ephemeral=True)

@tree.command(name="인증실패처벌", description="인증 오답 시 처벌 방식을 설정합니다.")
@app_commands.describe(punishment="인증 오답 시 적용할 처벌을 선택하세요.")
@app_commands.choices(
    punishment=[
        app_commands.Choice(name="추방", value="추방"),
        app_commands.Choice(name="차단", value="차단"),
        app_commands.Choice(name="역할 경고", value="역할 경고"),
        app_commands.Choice(name="다시 시도하게", value="다시 시도하게"),
    ]
)
async def set_auth_fail_punishment(interaction: discord.Interaction, punishment: app_commands.Choice[str]):
    auth_fail_punishments[interaction.guild.id] = punishment.value
    await interaction.response.send_message(f"⚠️ 인증 오답 시 '{punishment.value}' 처벌이 적용됩니다.", ephemeral=True)

@tree.command(name="봇사용채널", description="봇 사용 명령을 안내할 채널을 지정합니다.")
@app_commands.describe(channel="봇 사용 안내를 보낼 채널")
async def set_bot_use_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    bot_use_channels[interaction.guild.id] = channel.id
    await interaction.response.send_message(f"✅ 봇 사용 채널이 {channel.mention} 으로 설정되었습니다.", ephemeral=True)

@bot.event
async def on_member_join(member):
    guild = member.guild
    if guild.id not in auth_channels:
        return  # 인증 채널이 설정되지 않음

    channel_id = auth_channels[guild.id]
    channel = guild.get_channel(channel_id)
    if not channel:
        return

    code, image = generate_captcha()
    captcha_codes[member.id] = code

    timeout = auth_timeouts.get(guild.id, 60)  # 기본 60초
    await channel.send(
        f"👋 {member.mention}, 아래 이미지를 보고 정확하게 입력하세요! {timeout}초 안에!",
        file=discord.File(image, filename="captcha.png")
    )

    # 인증 대기 시간 적용
    async def expire_captcha():
        try:
            await asyncio.sleep(timeout)
            if member.id in captcha_codes:
                del captcha_codes[member.id]
                punishment = auth_punishments.get(guild.id, "다시 시도하게")
                try:
                    if punishment == "추방":
                        await member.kick(reason="인증 실패")
                        await channel.send(f"⏰ {member.mention} 인증 시간이 만료되어 서버에서 추방되었습니다.")
                    elif punishment == "차단":
                        await member.ban(reason="인증 실패", delete_message_days=0)
                        await channel.send(f"⏰ {member.mention} 인증 시간이 만료되어 서버에서 차단되었습니다.")
                    elif punishment == "역할 경고":
                        warn_role = discord.utils.get(guild.roles, name="경고")
                        if warn_role:
                            await member.add_roles(warn_role)
                            await channel.send(f"⏰ {member.mention} 인증 실패로 '경고' 역할이 부여되었습니다.")
                        else:
                            await channel.send(f"⚠️ '경고' 역할이 없어 처벌을 적용할 수 없습니다.")
                    else:  # 다시 시도하게
                        await channel.send(f"⏰ {member.mention} 인증 시간이 만료되었습니다. 다시 시도해주세요.")
                except Exception as e:
                    await channel.send(f"⚠️ 처벌 적용 중 오류: {e}")
        except Exception as e:
            print(f"[만료 타이머 오류] {e}")
    bot.loop.create_task(expire_captcha())

@bot.event
async def on_message(message):
    if message.author.bot:
        return

    guild = message.guild
    if not guild:
        return

    # 인증 시스템 처리
    if guild.id in auth_channels:
        channel_id = auth_channels[guild.id]
        if message.channel.id == channel_id:
            user_id = message.author.id
            if user_id in captcha_codes:
                if message.content.strip().upper() == captcha_codes[user_id]:
                    role = discord.utils.get(guild.roles, name="인증됨")
                    if role:
                        await message.author.add_roles(role)
                        await message.channel.send(f"✅ {message.author.mention} 인증 성공!")
                    else:
                        await message.channel.send("⚠ '인증됨' 역할이 없음! 서버에 역할을 먼저 만들어줘.")
                    del captcha_codes[user_id]
                else:
                    # 오답 처벌 적용
                    punishment = auth_fail_punishments.get(guild.id, "다시 시도하게")
                    try:
                        if punishment == "추방":
                            await message.author.kick(reason="인증 오답")
                            await message.channel.send(f"❌ {message.author.mention} 인증 오답으로 서버에서 추방되었습니다.")
                        elif punishment == "차단":
                            await message.author.ban(reason="인증 오답", delete_message_days=0)
                            await message.channel.send(f"❌ {message.author.mention} 인증 오답으로 서버에서 차단되었습니다.")
                        elif punishment == "역할 경고":
                            warn_role = discord.utils.get(guild.roles, name="경고")
                            if warn_role:
                                await message.author.add_roles(warn_role)
                                await message.channel.send(f"❌ {message.author.mention} 인증 오답으로 '경고' 역할이 부여되었습니다.")
                            else:
                                await message.channel.send(f"⚠️ '경고' 역할이 없어 처벌을 적용할 수 없습니다.")
                        else:  # 다시 시도하게
                            await message.channel.send("❌ 틀렸어! 다시 입력해봐.")
                    except Exception as e:
                        await message.channel.send(f"⚠️ 처벌 적용 중 오류: {e}")

    # up 명령어 감지 및 쿨다운 관리
    if message.content.lower().startswith('up '):
        user_id = message.author.id
        
        # 쿨다운 확인
        if user_id in user_up_cooldowns:
            remaining_time = user_up_cooldowns[user_id] - datetime.now()
            if remaining_time.total_seconds() > 0:
                minutes = int(remaining_time.total_seconds() // 60)
                seconds = int(remaining_time.total_seconds() % 60)
                await message.channel.send(f"⏰ {message.author.mention}님, 아직 {minutes}분 {seconds}초 남았습니다.")
                return
        
        # 1시간 쿨다운 설정
        user_up_cooldowns[user_id] = datetime.now() + timedelta(hours=1)
        
        # 알림 메시지 전송
        await message.channel.send(f"⏰ {message.author.mention}님이 다른 봇을 사용했습니다! 1시간 후에 다시 알림해드리겠습니다.")
        
        # 봇 사용 채널에 알림
        if guild.id in bot_use_channels:
            channel = guild.get_channel(bot_use_channels[guild.id])
            if channel and channel.id != message.channel.id:
                await channel.send(f"🎉 {message.author.mention}님이 다른 봇을 사용했습니다!")
        
        # 1시간 후 알림 전송
        async def up_reminder():
            await asyncio.sleep(3600)  # 1시간 대기
            if user_id in user_up_cooldowns:
                del user_up_cooldowns[user_id]
                await message.channel.send(f"⏰ {message.author.mention}님, 1시간이 지났습니다! 다시 사용할 수 있습니다.")
        
        asyncio.create_task(up_reminder())

    # bump 명령어 감지 및 쿨다운 관리
    if message.content.lower().startswith('bump '):
        user_id = message.author.id
        
        # 쿨다운 확인
        if user_id in user_bump_cooldowns:
            remaining_time = user_bump_cooldowns[user_id] - datetime.now()
            if remaining_time.total_seconds() > 0:
                hours = int(remaining_time.total_seconds() // 3600)
                minutes = int((remaining_time.total_seconds() % 3600) // 60)
                await message.channel.send(f"⏰ {message.author.mention}님, 아직 {hours}시간 {minutes}분 남았습니다.")
                return
        
        # 3시간 쿨다운 설정
        user_bump_cooldowns[user_id] = datetime.now() + timedelta(hours=3)
        
        # 알림 메시지 전송
        await message.channel.send(f"⏰ {message.author.mention}님이 다른 봇을 사용했습니다! 3시간 후에 다시 알림해드리겠습니다.")
        
        # 봇 사용 채널에 알림
        if guild.id in bot_use_channels:
            channel = guild.get_channel(bot_use_channels[guild.id])
            if channel and channel.id != message.channel.id:
                await channel.send(f"🚀 {message.author.mention}님이 다른 봇을 사용했습니다!")
        
        # 3시간 후 알림 전송
        async def bump_reminder():
            await asyncio.sleep(10800)  # 3시간 대기
            if user_id in user_bump_cooldowns:
                del user_bump_cooldowns[user_id]
                await message.channel.send(f"⏰ {message.author.mention}님, 3시간이 지났습니다! 다시 사용할 수 있습니다.")
        
        asyncio.create_task(bump_reminder())

@tree.command(name="경고", description="사용자에게 경고를 부여합니다.")
@app_commands.describe(user="경고를 부여할 사용자", reason="경고 사유")
async def warn_user(interaction: discord.Interaction, user: discord.Member, reason: str = "사유 없음"):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ 경고를 부여할 권한이 없습니다.", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    user_id = user.id
    
    # 경고 데이터 초기화
    if guild_id not in warnings:
        warnings[guild_id] = {}
    if user_id not in warnings[guild_id]:
        warnings[guild_id][user_id] = []
    
    # 경고 추가
    warning_data = {
        "reason": reason,
        "moderator": interaction.user.id,
        "timestamp": datetime.now().isoformat(),
        "warning_id": len(warnings[guild_id][user_id]) + 1
    }
    warnings[guild_id][user_id].append(warning_data)
    
    # 경고 횟수 확인
    warning_count = len(warnings[guild_id][user_id])
    
    # 경고 한계 확인 및 처벌
    if guild_id in warn_limits:
        limits = warn_limits[guild_id]
        if "추방" in limits and warning_count >= limits["추방"]:
            try:
                await user.kick(reason=f"경고 {limits['추방']}회 누적")
                await interaction.response.send_message(f"⚠️ {user.mention}님에게 경고를 부여했습니다. (총 {warning_count}회)\n🚫 경고 {limits['추방']}회 누적으로 추방되었습니다.")
                return
            except:
                await interaction.response.send_message(f"⚠️ {user.mention}님에게 경고를 부여했습니다. (총 {warning_count}회)\n❌ 추방 권한이 없어 처벌을 적용할 수 없습니다.")
                return
        
        if "차단" in limits and warning_count >= limits["차단"]:
            try:
                await user.ban(reason=f"경고 {limits['차단']}회 누적", delete_message_days=0)
                await interaction.response.send_message(f"⚠️ {user.mention}님에게 경고를 부여했습니다. (총 {warning_count}회)\n🚫 경고 {limits['차단']}회 누적으로 차단되었습니다.")
                return
            except:
                await interaction.response.send_message(f"⚠️ {user.mention}님에게 경고를 부여했습니다. (총 {warning_count}회)\n❌ 차단 권한이 없어 처벌을 적용할 수 없습니다.")
                return
    
    await interaction.response.send_message(f"⚠️ {user.mention}님에게 경고를 부여했습니다. (총 {warning_count}회)\n📝 사유: {reason}")

@tree.command(name="경고조회", description="사용자의 경고 목록을 조회합니다.")
@app_commands.describe(user="경고를 조회할 사용자")
async def check_warnings(interaction: discord.Interaction, user: discord.Member):
    guild_id = interaction.guild.id
    user_id = user.id
    
    if guild_id not in warnings or user_id not in warnings[guild_id]:
        await interaction.response.send_message(f"✅ {user.mention}님은 경고가 없습니다.", ephemeral=True)
        return
    
    user_warnings = warnings[guild_id][user_id]
    if not user_warnings:
        await interaction.response.send_message(f"✅ {user.mention}님은 경고가 없습니다.", ephemeral=True)
        return
    
    embed = discord.Embed(title=f"📋 {user.display_name}님의 경고 목록", color=0xff6b6b)
    embed.set_thumbnail(url=user.display_avatar.url)
    
    for warning in user_warnings:
        moderator = interaction.guild.get_member(warning["moderator"])
        mod_name = moderator.display_name if moderator else "알 수 없음"
        timestamp = datetime.fromisoformat(warning["timestamp"]).strftime("%Y-%m-%d %H:%M")
        
        embed.add_field(
            name=f"경고 #{warning['warning_id']}",
            value=f"📝 사유: {warning['reason']}\n👮 관리자: {mod_name}\n⏰ 시간: {timestamp}",
            inline=False
        )
    
    embed.set_footer(text=f"총 {len(user_warnings)}개의 경고")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="경고삭제", description="사용자의 특정 경고를 삭제합니다.")
@app_commands.describe(user="경고를 삭제할 사용자", warning_id="삭제할 경고 번호")
async def remove_warning(interaction: discord.Interaction, user: discord.Member, warning_id: int):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ 경고를 삭제할 권한이 없습니다.", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    user_id = user.id
    
    if guild_id not in warnings or user_id not in warnings[guild_id]:
        await interaction.response.send_message(f"❌ {user.mention}님은 경고가 없습니다.", ephemeral=True)
        return
    
    user_warnings = warnings[guild_id][user_id]
    if warning_id < 1 or warning_id > len(user_warnings):
        await interaction.response.send_message(f"❌ 경고 번호 {warning_id}가 존재하지 않습니다.", ephemeral=True)
        return
    
    # 경고 삭제
    removed_warning = user_warnings.pop(warning_id - 1)
    
    # 경고 번호 재정렬
    for i, warning in enumerate(user_warnings):
        warning["warning_id"] = i + 1
    
    await interaction.response.send_message(f"✅ {user.mention}님의 경고 #{warning_id}를 삭제했습니다.\n📝 삭제된 경고 사유: {removed_warning['reason']}")

@tree.command(name="경고초기화", description="사용자의 모든 경고를 초기화합니다.")
@app_commands.describe(user="경고를 초기화할 사용자")
async def clear_warnings(interaction: discord.Interaction, user: discord.Member):
    if not interaction.user.guild_permissions.manage_messages:
        await interaction.response.send_message("❌ 경고를 초기화할 권한이 없습니다.", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    user_id = user.id
    
    if guild_id not in warnings or user_id not in warnings[guild_id]:
        await interaction.response.send_message(f"❌ {user.mention}님은 경고가 없습니다.", ephemeral=True)
        return
    
    warning_count = len(warnings[guild_id][user_id])
    del warnings[guild_id][user_id]
    
    await interaction.response.send_message(f"✅ {user.mention}님의 모든 경고({warning_count}개)를 초기화했습니다.")

@tree.command(name="경고한계설정", description="경고 횟수에 따른 처벌 한계를 설정합니다.")
@app_commands.describe(kick_warnings="추방까지 필요한 경고 횟수", ban_warnings="차단까지 필요한 경고 횟수")
async def set_warn_limits(interaction: discord.Interaction, kick_warnings: int = 3, ban_warnings: int = 5):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ 관리자 권한이 필요합니다.", ephemeral=True)
        return
    
    guild_id = interaction.guild.id
    warn_limits[guild_id] = {
        "추방": kick_warnings,
        "차단": ban_warnings
    }
    
    await interaction.response.send_message(f"✅ 경고 한계가 설정되었습니다.\n🚫 추방: {kick_warnings}회\n🚫 차단: {ban_warnings}회", ephemeral=True)

    await bot.process_commands(message)

bot.run(os.getenv("TOKEN"))
