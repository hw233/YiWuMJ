# -*- coding:utf-8 -*-
import copy
import datetime
import json
import re

import const
from KBEDebug import *
from SimpleCache import *

# 表拥有的列 当出现表修改时自动更新
RECORD_TABLE_COLUMNS_DICT = {
	"record_id": "record_id INT",
	"player_id_list": "player_id_list VARCHAR(255) NOT NULL",
	"record": "record TEXT NOT NULL",
	"create_time": "create_time DATETIME",
	"game_name": "game_name varchar(64)",
	"club_id": "club_id INTEGER",
	"room_id": "room_id INTEGER NOT NULL DEFAULT -1 ",
}


class iRoomRecord(object):
	def __init__(self):
		# self.recordList = []
		# record id -- record
		self.recordCache = SimpleCache(const.MAX_RECORD_CACHE)
		self.recordNoneCache = SimpleCache(const.MAX_RECORD_NONE_CACHE)
		# 没有打完的牌局不记录数据库
		self.transientRecordDict = {}
		self.roomRecordIdDict = {}
		# self.recordId = 0
		# self.recordList = []
		# DEBUG_MSG("recordList size {0} {1}".format(len(self.recordList), self.recordIndex))
		self.init_database()

		self.start_clean_cache_timer()

	def start_clean_cache_timer(self):
		def callback():
			DEBUG_MSG("clean record cache before: non-none: {0} , none: {1}"
					  .format(len(self.recordCache), len(self.recordNoneCache)))
			self.recordCache.clean_cache(const.CLEAN_RECORD_CACHE_IDLE_INTERVAL)
			self.recordNoneCache.clean_cache(const.CLEAN_RECORD_CACHE_IDLE_INTERVAL)
			DEBUG_MSG("clean record cache after: non-none: {0} , none: {1}"
					  .format(len(self.recordCache), len(self.recordNoneCache)))

		self.add_repeat_timer(const.CLEAN_RECORD_CACHE_INTERVAL, const.CLEAN_RECORD_CACHE_INTERVAL, callback)

	def begin_record_room(self, mailbox, room_id, game_room, dice_list):
		rid = self.recordIndex + 1
		self.recordIndex = rid
		self.roomRecordIdDict[room_id] = rid

		players_list = game_room.origin_players_list
		init_tiles = [None] * len(players_list)
		player_id_list = []
		for p in players_list:
			init_tiles[p.idx] = copy.copy(p.tiles)
			player_id_list.append(p.mb.userId)

		record = {
			'recordId': rid,
			'init_info': game_room.get_init_client_dict(),
			'dice_list': copy.deepcopy(dice_list),
			'player_id_list': player_id_list,
			'init_tiles': init_tiles,
			'prevailing_wind': game_room.prevailing_wind,
			'kingTiles': copy.copy(game_room.kingTiles),
			'wreathsList': copy.copy(game_room.wreathsList),
			'start_time': time.time(),
			'roomId': room_id,
			'clubId': game_room.club.clubId if game_room.club is not None else -1,
		}
		self.transientRecordDict[rid] = record
		mailbox.begin_record_callback(rid)

	def end_record_room(self, room_id, game_room, result_info):
		rid = self.roomRecordIdDict[room_id]
		record = self.transientRecordDict[rid]
		record['op_record_list'] = json.dumps(game_room.op_record)
		# record['op_special_record_list'] = game_room.op_special_record
		record['round_result'] = result_info
		record['end_time'] = time.time()

		record_str = json.dumps(record)

		del self.transientRecordDict[rid]
		del self.roomRecordIdDict[room_id]
		# self._add_cache(rid, record)
		self._insert_record(rid, record['player_id_list'], record_str, room_id, game_room.club.clubId if game_room.club is not None else -1)

	def give_up_record_room(self, room_id):
		if self.roomRecordIdDict.__contains__(room_id):
			rid = self.roomRecordIdDict[room_id]
			del self.transientRecordDict[rid]
			del self.roomRecordIdDict[room_id]

	def query_record(self, mailbox, record_id):
		DEBUG_MSG("queryRecord {0} ".format(record_id))
		cached, record = self._get_from_cache(record_id)
		if cached:
			mailbox.queryRecordResult(record)
		else:
			def query_callback_with_cache(results, rows, insert_id, error):
				DEBUG_MSG("query result: {0}, {1}, {2}, {3}".format(len(results), rows, insert_id, error))
				if len(results) > 0:
					record = results[0][1].decode()
					mailbox.queryRecordResult(record)
					self._add_cache(record_id, record)
				else:
					mailbox.queryRecordResult(None)
					self._add_cache(record_id, None)

			self._query_record_from_db(record_id, query_callback_with_cache)

	def query_user_record(self, mailbox, user_id, size):
		pass

	def _get_from_cache(self, record_id):
		if self.recordNoneCache.__contains__(record_id):
			self.recordNoneCache.update_cache_time(record_id)
			return True, None
		data = self.recordCache[record_id]
		if data is None:
			return False, None
		return True, self.recordCache[record_id]

	def _add_cache(self, record_id, record):
		DEBUG_MSG("add cache rid: ", record_id)
		if record is None:
			self.recordNoneCache[record_id] = None
		else:
			del self.recordNoneCache[record_id]
			self.recordCache[record_id] = record

	def init_database(self):
		def create_callback(result, rows, insert_id, error):
			DEBUG_MSG("room record init_database: {0}, {1}, {2}, {3}".format(result, rows, insert_id, error))
			self._update_table_column()

		KBEngine.executeRawDatabaseCommand(
			'CREATE TABLE IF NOT EXISTS {0} ( id INT NOT NULL AUTO_INCREMENT,record_id INT,player_id_list VARCHAR(255) NOT NULL, record TEXT NOT NULL, create_time DATETIME,game_name varchar(64),club_id INTEGER, room_id INTEGER NOT NULL DEFAULT -1 ,PRIMARY KEY (id)) DEFAULT CHARSET = utf8;'
				.format(const.TABLE_GAME_RECORD_NAME),
			create_callback)

	def _update_table_column(self):
		""" 如果存在不需要的列，现在不删除多余的列 """

		def update_callback(result, rows, insert_id, error):
			DEBUG_MSG("room record update table column: {0}, {1}, {2}, {3}".format(result, rows, insert_id, error))
			if error is not None:
				ERROR_MSG("room record update_table_column error", error)

		def show_callback(result, rows, insert_id, error):
			result_str = ''

			columns = RECORD_TABLE_COLUMNS_DICT.keys()
			exist_columns = []
			if result is not None and len(result) > 0:
				for r in result:
					exist_columns.append(r[0].decode())
					result_str += r[0].decode() + ', '
			DEBUG_MSG("room record show columns callback: {0}, {1}, {2}, {3}".format(result_str, rows, insert_id, error))
			update_sql = "ALTER TABLE {0} ".format(const.TABLE_GAME_RECORD_NAME)
			for column in columns:
				if column not in exist_columns:
					update_sql += "add " + RECORD_TABLE_COLUMNS_DICT[column] + ","
			update_sql = update_sql[:-1] + ';'
			DEBUG_MSG("room record update table sql: {}".format(update_sql))
			KBEngine.executeRawDatabaseCommand(update_sql, update_callback)

		KBEngine.executeRawDatabaseCommand("SHOW COLUMNS FROM  {0};".format(const.TABLE_GAME_RECORD_NAME), show_callback)

	def _insert_record(self, record_id, player_id_list, record, room_id, club_id):
		def insert_callback(result, rows, insert_id, error):
			DEBUG_MSG('insert_callback {0}, {1}, {2}, {3}'.format(result, rows, insert_id, error))

		del self.recordNoneCache[record_id]
		sql = 'INSERT INTO {0} (record_id, player_id_list,record,create_time,room_id,club_id,game_name) VALUES ("{1}", "{2}","{3}","{4}","{5}","{6}","{7}")' \
			.format(const.TABLE_GAME_RECORD_NAME, record_id, json.dumps(player_id_list), re.escape(record),
					datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), room_id, club_id, const.GAME_NAME)
		KBEngine.executeRawDatabaseCommand(sql, insert_callback)

	def _query_record_from_db(self, record_id, callback):
		sql = 'SELECT record_id, record, create_time FROM {0} WHERE record_id = "{1}";' \
			.format(const.TABLE_GAME_RECORD_NAME, record_id)
		KBEngine.executeRawDatabaseCommand(sql, callback)
