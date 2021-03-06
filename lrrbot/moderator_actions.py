import asyncio
import datetime
import sqlalchemy

from common import pubsub
from common import utils
from common import time
from common import slack
from common.config import config
import logging

log = logging.getLogger("moderator_actions")

class ModeratorActions:
	def __init__(self, lrrbot, loop):
		self.lrrbot = lrrbot
		self.loop = loop

		self.last_ban = None

		users = self.lrrbot.metadata.tables["users"]
		with self.lrrbot.engine.begin() as conn:
			selfrow = conn.execute(sqlalchemy.select([users.c.id]).where(users.c.name == config['username'])).first()
			targetrow = conn.execute(sqlalchemy.select([users.c.id]).where(users.c.name == config['channel'])).first()
		if selfrow is not None and targetrow is not None:
			self_channel_id, = selfrow
			target_channel_id, = targetrow
			topic = "chat_moderator_actions.%s.%s" % (self_channel_id, target_channel_id)

			self.lrrbot.pubsub.subscribe([topic])
			pubsub.signals.signal(topic).connect(self.on_message)

	def on_message(self, sender, message):
		action = message['data']['moderation_action']
		args = message['data']['args']
		mod = message['data']['created_by']

		if action == 'timeout':
			user = args[0]
			length = int(args[1])
			reason = args[2] if len(args) >= 3 else None
			logid, attachments = self.get_chat_log(user)
			same_user = (self.last_ban == (user.lower(), logid))
			if same_user:
				attachments = []
			self.last_ban = (user.lower(), logid)

			text = "%s was%s timed out for %s by %s." % (slack.escape(user), " also" if same_user else "", slack.escape(time.nice_duration(length, 0)), slack.escape(mod))
			if reason is not None:
				text += " Reason: %s" % slack.escape(reason)
		elif action == 'ban':
			user = args[0]
			reason = args[1] if len(args) >= 2 else None
			logid, attachments = self.get_chat_log(user)
			same_user = (self.last_ban == (user.lower(), logid))
			if same_user:
				attachments = []
			self.last_ban = (user.lower(), logid)

			text = "%s was%s banned by %s." % (slack.escape(user), " also" if same_user else "", slack.escape(mod))
			if reason is not None:
				text += " Reason: %s" % slack.escape(reason)
		elif action == 'unban':
			user = args[0]
			attachments = []
			self.last_ban = None

			text = "%s was unbanned by %s." % (slack.escape(user), slack.escape(mod))
		elif action == 'untimeout':
			user = args[0]
			attachments = []
			self.last_ban = None

			text = "%s was untimed-out by %s." % (slack.escape(user), slack.escape(mod))
		else:
			log.info("Got unrecognised message: %r", message['data'])
			attachments = []
			self.last_ban = None

			text = "%s did a %s: %s" % (slack.escape(mod), slack.escape(action), slack.escape(repr(args)))

		asyncio.async(slack.send_message(text, attachments=attachments), loop=self.loop).add_done_callback(utils.check_exception)

	def get_chat_log(self, user):
		attachments = []
		now = datetime.datetime.now(config["timezone"])

		log = self.lrrbot.metadata.tables["log"]
		with self.lrrbot.engine.begin() as conn:
			rows = conn.execute(sqlalchemy.select([log.c.id, log.c.time, log.c.message])
				.where(log.c.source == user.lower())
				.where(log.c.time > now - datetime.timedelta(days=1))
				.limit(3)
				.order_by(log.c.time.asc())).fetchall()

		logid = -1
		for logid, timestamp, message in rows:
			timestamp = timestamp.astimezone(config["timezone"])
			attachments.append({
				'text': slack.escape("%s (%s ago): %s" % (timestamp.strftime("%H:%M"), time.nice_duration(now - timestamp), message))
			})

		return logid, attachments
