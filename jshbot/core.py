import asyncio
import discord
import logging
import os.path
import copy
import time
import sys
import os

# Debug
import traceback

from jshbot import configurations, plugins, commands, parser, data
from jshbot.exceptions import ErrorTypes, BotException

EXCEPTION = 'Core'

class Bot(discord.Client):

    def __init__(self, start_file, debug):
        self.version = '0.3.0-alpha'
        self.date = 'May 19th, 2016'
        self.time = int(time.time())
        self.readable_time = time.strftime('%c')
        self.debug = debug

        if self.debug:
            logging.debug("=== Starting up JshBot {} ===".format(self.version))
            logging.debug("=== Time: {} ===".format(self.readable_time))
        else:
            print("=== Starting up JshBot {} ===".format(self.version))
            print("=== Time: {} ===".format(self.readable_time))

        super().__init__()

        self.path = os.path.split(os.path.realpath(start_file))[0]
        logging.debug("Setting directory to {}".format(self.path));

        logging.debug("Loading plugins and commands...")
        self.commands = {}
        self.manual = {}
        self.plugins = plugins.get_plugins(self)

        logging.debug("Setting up data...")
        self.data = {'global_users':{}, 'global_plugins':{}}
        self.volatile_data = copy.copy(self.data)
        self.data_changed = False

        logging.debug("Loading configurations...")
        self.configurations = configurations.get_configurations(self)

        # Extras
        self.edit_dictionary = {}

    def interrupt_say(self, channel_id, message, channel=None):
        '''
        Allows plugins to send messages without having to return directly from
        get_response. This should mostly be avoided, and just used for errors
        or other immediately relevant notifications.
        '''
        if not channel:
            try:
                channel = discord.utils.get(
                        self.get_all_channels(), id=channel_id)
            except:
                raise BotException(ErrorTypes.RECOVERABLE, EXCEPTION,
                        "Server {} could not be found.".format(server_id))
        asyncio.ensure_future(self.send_message(channel, message))

    def get_token(self):
        return self.configurations['core']['token']

    def usage_reminder(self, base):
        '''
        Uses the base module to get the usage reminder for a command.
        '''
        base_module = self.plugins['base'][0]
        return base_module.get_usage_reminder(self, base)

    def can_respond(self, message):
        '''
        Determines whether or not the bot can respond to the given message.
        Checks that the message has text, matches an invoker, and that the
        server/channel/user is not muted or blocked. Admins/moderators override.
        If the message is a direct message, respond if there is a valid invoker.
        Returns the formatted content for the specific invoker, otherwise if
        the bot cannot respond, returns None.
        '''

        # Ignore empty messages and messages by bots
        if (not message.content or message.author.bot or
                message.author.id == self.user.id):
            return None

        # Check that the message starts with a valid invoker
        content = message.content
        has_regular_invoker = False
        has_mention_invoker = False
        has_name_invoker = False
        has_nick_invoker = False
        if message.channel: # Get custom invokers if they exist
            server_data = data.get(self, 'base', None, message.server.id,
                    default={})
            invokers = [server_data.get('command_invoker', None)]
            #invokers = [data.get(
            #        self, 'base', 'command_invoker', message.server.id)]
            if not invokers[0]:
                invokers = self.configurations['core']['command_invokers']
        else:
            invokers = self.configurations['core']['command_invokers']
        for invoker in invokers:
            if content.startswith(invoker):
                has_regular_invoker = True
                break
        if not has_regular_invoker:
            has_mention_invoker = content.startswith(
                    ('<@' + self.user.id + '>', '<@!' + self.user.id + '>'))
            if not has_mention_invoker:
                clean_content = content.lower()
                has_name_invoker = clean_content.startswith(
                        self.user.name.lower() + ' ')
                if not has_name_invoker and not message.channel.is_private:
                    has_nick_invoker = clean_content.startswith(
                            message.server.me.nick.lower() + ' ')
                    if has_nick_invoker: # Clean up content (nickname)
                        content = content[len(message.server.me.nick):].strip()
                else: # Clean up content (name)
                    content = content[len(self.user.name):].strip()
            else: # Clean up content (mention)
                content = content.partition(' ')[2].strip()
        else: # Clean up content (invoker)
            content = content.partition(invoker)[2].strip()

        # Bot must respond to mentions only
        if (self.configurations['core']['mention_mode'] or
                data.get(self, 'base', 'mention_mode',
                    server_id=message.server.id, default=False)):
            if not (has_mention_invoker or has_name_invoker or
                    has_nick_invoker):
                return None
        else: # Any invoker will do
            if not (has_regular_invoker or has_mention_invoker or
                    has_name_invoker or has_nick_invoker):
                return None

        # Respond to direct messages
        if message.channel.is_private:
            return content

        author_id = message.author.id

        try:
            # Owners/moderators override everything
            # This is faster than calling the function in jshbot.data
            channel_id = message.channel.id
            if (author_id in self.configurations['core']['owners'] or
                    author_id in server_data.get('moderators', []) or
                    author_id == message.server.owner.id):
                return content
            # Server/channel muted, or user is blocked
            if (server_data.get('muted', False) or
                    (channel_id in server_data.get('muted_channels', [])) or
                    (author_id in server_data.get('blocked', []))):
                return None
        except KeyError as e: # Bot may not have updated fast enough
            logging.warn("Failed to find server in can_respond(): " + str(e))
            data.check_all(self)
            return None # Don't recurse for safety

        return content # Clear to respond

    async def on_message(self, message, replacement_message=None):
        plugins.broadcast_event(self, 2, message)

        # Ensure bot can respond properly
        content = self.can_respond(message)
        if not content:
            return

        # Ensure command is valid
        split_content = content.split(' ', 1)
        if len(split_content) == 1: # No spaces
            split_content.append('')
        base, parameters = split_content
        command_pair, shortcut = commands.get_command_pair(self, base)
        if not command_pair: # Suitable command not found
            logging.debug("Suitable command not found: " + base)
            return

        # Bot is clear to get response. Send typing to signify
        if (not replacement_message and
                self.configurations['core']['send_typing']):
            await self.send_typing(message.channel)

        # Parse command and reply
        try:
            print(message.author.name + ': ' + message.content)
            parsed_command = parser.parse(
                    self, base, parameters, command_pair, shortcut)
            print('\t' + str(parsed_command))
            response = await (commands.execute(self, message, parsed_command))
        except BotException as e: # Respond with error message
            response = (str(e), False, 0, None)
        except Exception as e: # General error
            logging.error(e)
            traceback.print_exc()
            error = 'Uh oh. The bot encountered an exception: {0}: {1}'.format(
                    type(e).__name__, e)
            response = (error, False, 0, None)

        # If a replacement message is given, edit it
        if replacement_message:
            message_reference = await self.edit_message(
                    replacement_message, response[0])
        else:
            message_reference = await self.send_message(
                    message.channel, response[0], tts=response[1])

        # A response looks like this:
        # (text, tts, message_type, extra)
        # message_type can be:
        # 0 - normal
        # 1 - permanent
        # 2 - terminal (deletes itself after 'extra' seconds)
        # 3 - active (pass the reference back to the plugin to edit)
        # If message_type is >= 1, do not add to the edit dictionary

        if response[2] == 0: # Normal
            # Edited commands are handled in base.py
            wait_time = self.configurations['core']['edit_timeout']
            if wait_time:
                self.edit_dictionary[message.id] = message_reference
                await asyncio.sleep(wait_time)
                if message.id in self.edit_dictionary:
                    del self.edit_dictionary[message.id]

        elif response[2] == 2: # Terminal
            if not response[3]:
                response[3] = 10
            await asyncio.sleep(int(response[3]))
            await self.delete_message(message_reference)

        elif response[2] == 3: # Active
            await commands.handle_active_message(self, message_reference,
                    parsed_command, response[3])

    async def on_ready(self):
        # Make sure server data is ready
        data.check_all(self)
        data.load_data(self)

        plugins.broadcast_event(self, 0)

        if self.debug:
            logging.debug("=== {} online ===".format(self.user.name))
        else:
            print("=== {} online ===".format(self.user.name))

        await self.save_loop()

    async def on_error(self, event, *args, **kwargs):
        plugins.broadcast_event(self, 1, event, *args, **kwargs)
    async def on_socket_raw_receive(self, msg):
        plugins.broadcast_event(self, 3, msg)
    async def on_socket_raw_send(self, payload):
        plugins.broadcast_event(self, 4, payload)
    async def on_message_delete(self, message):
        plugins.broadcast_event(self, 5, message)
    async def on_message_edit(self, before, after):
        plugins.broadcast_event(self, 6, before, after)
    async def on_channel_delete(self, channel):
        plugins.broadcast_event(self, 7, channel)
    async def on_channel_create(self, channel):
        plugins.broadcast_event(self, 8, channel)
    async def on_channel_update(self, before, after):
        plugins.broadcast_event(self, 9, before, after)
    async def on_member_join(self, member):
        plugins.broadcast_event(self, 10, member)
    async def on_member_update(self, before, after):
        plugins.broadcast_event(self, 11, before, after)
    async def on_server_join(self, server):
        plugins.broadcast_event(self, 12, server)
    async def on_server_remove(self, server):
        plugins.broadcast_event(self, 13, server)
    async def on_server_update(self, before, after):
        plugins.broadcast_event(self, 14, before, after)
    async def on_server_role_create(self, server, role):
        plugins.broadcast_event(self, 15, server, role)
    async def on_server_role_delete(self, server, role):
        plugins.broadcast_event(self, 16, server, role)
    async def on_server_role_update(self, before, after):
        plugins.broadcast_event(self, 17, before, after)
    async def on_server_available(self, server):
        plugins.broadcast_event(self, 18, server)
    async def on_server_unavailable(self, server):
        plugins.broadcast_event(self, 19, server)
    async def on_voice_state_update(self, before, after):
        plugins.broadcast_event(self, 20, before, after)
    async def on_member_ban(self, member):
        plugins.broadcast_event(self, 21, member)
    async def on_member_unban(self, server, user):
        plugins.broadcast_event(self, 22, server, user)
    async def on_typing(self, channel, user, when):
        plugins.broadcast_event(self, 23, channel, user, when)

    async def save_loop(self):
        '''
        Runs the loop that periodically saves data.
        '''
        try:
            interval = int(self.configurations['core']['save_interval'])
            interval = 0 if interval <= 0 else interval * 60
        except:
            logging.warn("Saving interval not found in the configuration file.")
            interval = 0
        while interval:
            await asyncio.sleep(interval)
            self.save_data()

    def save_data(self):
        logging.debug("Saving data...")
        data.save_data(self)
        logging.debug("Saving data complete.")

    def restart(self):
        logging.debug("Attempting to restart the bot...")
        self.save_data()
        asyncio.ensure_future(self.logout())
        os.system('python3.5 ' + self.path + '/start.py')

    def shutdown(self):
        logging.debug("Writing data on shutdown...")
        self.save_data()
        logging.debug("Closing down!")
        try:
            asyncio.ensure_future(self.logout())
        except:
            pass
        sys.exit()

def initialize(start_file, debug=False):
    if debug:
        logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
    bot = Bot(start_file, debug)
    bot.run(bot.get_token())
    logging.error("Bot disconnected. Shutting down...")
    bot.shutdown()

