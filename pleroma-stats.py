#!/usr/bin/env python
# -*- coding: utf-8 -*-

from six.moves import urllib
import datetime
#from datetime import datetime
from subprocess import call
import time
import threading
import os
import json
import time
import signal
import sys
import os.path        # For checking whether config.txt file exists
import requests       # For doing the web stuff, dummy!
import operator       # allow assigning dictionary values to a variable 15/07/18
import calendar
import psycopg2
from psycopg2.extras import Json
from psycopg2.extras import register_default_json
from psycopg2.extras import RealDictCursor
import traceback
from importlib import reload

reload(sys)

###############################################################################
# INITIALISATION
###############################################################################

def get_parameter( parameter, file_path ):
    # Check if config.txt file exists
    if not os.path.isfile(file_path):
        print("File %s not found, exiting."%file_path)
        sys.exit(0)

    # Find parameter in file
    with open( file_path ) as f:
        for line in f:
            if line.startswith( parameter ):
                return line.replace(parameter + ":", "").strip()

    # Cannot find parameter, exit
    print(file_path + "  Missing parameter %s "%parameter)
    sys.exit(0)

# Load configuration from config file
config_filepath = "config.txt"

pleroma_hostname = get_parameter("pleroma_hostname", config_filepath) # E.g., pleroma.site
pleroma_db = get_parameter("pleroma_db", config_filepath) # E.g., pleroma
pleroma_db_user = get_parameter("pleroma_db_user", config_filepath) # E.g., pleroma
pleroma_db_password = get_parameter("pleroma_db_pass", config_filepath)
grafana_db = get_parameter("grafana_db", config_filepath) # E.g., grafana_prod

# Postgres connection strings
cstring_grafana = "dbname=" + grafana_db + " user=" + pleroma_db_user + " password=" + pleroma_db_password + " host='localhost' port='5432'"
cstring_pleroma = "dbname=" + pleroma_db + " user=" + pleroma_db_user + " password=" + pleroma_db_password + " host='localhost' port='5432'"

###################################################################################
# GET THE DATA from Pleroma server's API: user_count, domain_count and status count
# ** DEACTIVATED ** See line 115 **
###################################################################################

#res = requests.get('https://' + pleroma_hostname + '/api/v1/instance?')

#current_users = res.json()['stats']['user_count']
#num_servers = res.json()['stats']['domain_count']
#num_posts = res.json()['stats']['status_count']

# Posts per user
#posts_per_user = int (num_posts / current_users)

################################################################################
# get the federated hosts from Pleroma`s DB, table users
################################################################################

try:

      conn = None
      conn = psycopg2.connect(cstring_pleroma)

      cur = conn.cursor()

      # Pleroma's developers did it this way to get federated servers: SELECT distinct split_part(nickname, '@', 2) FROM users;
      # we need federated servers but also federated users
      cur.execute("select distinct ap_id AS host FROM users WHERE local='f'") # CHANGED FROM ORIGINAL ("as_id" INSTEAD OF "id")

      host_federats = []

      for row in cur:
         host_federats.append(row[0]) ## store hosts's urls to host_federats[] array

      fed_users = len(host_federats)  # how many federated users

      federated_url = []
      i = 0
      new_url = ''
      sep_right = '/users'
      sep_left = '(https://'

      while i < len(host_federats)-1:

        new_url = host_federats[i].rpartition(sep_right)
        new_url = new_url[0]
        new_url = new_url.partition(sep_left)

        federated_url.append(new_url[2])

        i += 1

      federated_url = sorted(set(federated_url)) # ordered list of federated servers

      ###############################################################################################
      # GETTING user_count, domain_count and status_count from Pleroma's API is not the best choice
      # because Pleroma code schedule update stats each hour.
      # Better get them from the Pleroma's DB to realtime counters
      ###############################################################################################

      # get user_count from Pleroma's DB
      cur.execute("select count(id) from users where local='t' AND is_active='t' AND email IS NOT NULL") # CHANGED FROM ORIGINAL ("is_active='t' INSTEAD OF "deactivated=false"
      current_users = cur.fetchone()[0]

      # get federated servers from Pleroma's DB
      cur.execute("SELECT COUNT (distinct split_part(nickname, '@', 2)) FROM users where local='f'")
      num_servers = cur.fetchone()[0]

      # get status_count from Pleroma's DB
      cur.execute("select SUM(note_count) FROM users WHERE local='t'")
      num_posts = cur.fetchone()[0]

      #############################################################################################
      # 21.5.19 *New* Get last hour federated posts
      cur.execute("select count(inserted_at) from activities WHERE data->>'type'='Create' AND inserted_at  > current_timestamp - INTERVAL '180 minutes'")
      fed_posts_last_hour = cur.fetchone()[0]

      ############################################################################################
      # 22.5.19 *New* Get how many disc space is using the Pleroma's DB.
      cur.execute("select pg_database_size(%s)", (pleroma_db,))
      db_disk_space = (cur.fetchone()[0] / 1024) / 1024  ## to transform bytes to MB

      cur.close()

except (Exception, psycopg2.DatabaseError) as error:

      print(traceback.format_exc())
      sys.exit(':-(')

finally:

      if conn is not None:

        conn.close()

#####################################################################################################
# some calcs
#####################################################################################################

# Posts per user
posts_per_user = int (num_posts / current_users)

#####################################################################################################
# get unreachable hosts from Pleroma's DB table instances                                           #
#  id |     host     |     unreachable_since	  |        inserted_at         |         updated_at #
#####################################################################################################

ara = datetime.datetime.now()

try:

      conn = None
      conn = psycopg2.connect(cstring_pleroma)

      cur = conn.cursor()

      cur.execute("select host, unreachable_since, inserted_at from instances where unreachable_since IS NOT NULL;")

      hosts_unreached = []
      hosts_unreached_since = []
      elapsed_days = []
      inserted = []

      for row in cur:

         hosts_unreached.append(row[0])            ## store unreached hosts to hosts_unreached[] array
         hosts_unreached_since.append(str(row[1])) ## store timestamp since which the host is unreacheable to hosts_unreached_since[] array
         elapsed_days.append(ara-row[1])
         inserted.append(str(row[2]))

      unreachable_hosts = len(hosts_unreached)

      cur.close()

except (Exception, psycopg2.DatabaseError) as error:

      print (traceback.format_exc())
      sys.exit(':-(')

finally:

      if conn is not None:

        conn.close()

##########################################################################################
# store unreachable hosts data to grafana DB, table unreached_servers
#  columns server | since | days | inserted_at | datetime
#########################################################################################

insert_row = """INSERT INTO unreached_servers(server, since, days, inserted_at)
             VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING;"""
conn = None

i = 0

while i < (len(hosts_unreached)):

  try:

    conn = psycopg2.connect(cstring_grafana)

    cur = conn.cursor()

    # execute INSERT server, since, days, inserted_at
    cur.execute(insert_row, (hosts_unreached[i], hosts_unreached_since[i], elapsed_days[i], inserted[i]))

    # execute UPDATE
    cur.execute("UPDATE unreached_servers SET days=(%s) where server=(%s)", (elapsed_days[i], hosts_unreached[i]))
    cur.execute("UPDATE unreached_servers SET datetime=(%s) where server=(%s)", (ara, hosts_unreached[i]))    

    # delete back on life servers
    cur.execute("DELETE from unreached_servers where datetime <> %s", (ara,))

    # commit data
    conn.commit()

    # close the connection
    cur.close()

    i = i+1

  except (Exception, psycopg2.DatabaseError) as error:

    print(traceback.format_exc())
    sys.exit(':-(')

  finally:

    if conn is not None:

      conn.close()

#################################################################################
# definition of intial values for the very first time running
#################################################################################

users_before = current_users
posts_before = num_posts
servers_before = num_servers
users_hour = 0
users_day = 0
users_week = 0
posts_hour = 0
servers_hour = 0
active = 0
active30= 0
posts_active = 0
interactions = 0
fed_users_hour = 0
fed_users_before = fed_users
inc_disc_space_hour = 0

#################################################################################
# Connect to Grafana's Postgresql DB to check if is empty (0 rows), table stats
#################################################################################

try:

  conn = None
  conn = psycopg2.connect(cstring_grafana)

  cur = conn.cursor()

  cur.execute("SELECT * from stats")
  row = cur.fetchone()
  if row:

    ##############################################################################################################################################################
    # Connect to Grafana's Postgresql DB to fetch last row local users, posts, servers, federated users and stored used_disk_space of the whole Pleroma's database
    ##############################################################################################################################################################

    try:

      conn = None
      conn = psycopg2.connect(cstring_grafana)

      cur = conn.cursor()

      cur.execute("SELECT DISTINCT ON (datetime) users,posts,servers,federated_users,used_disk_space,datetime FROM stats WHERE datetime > current_timestamp - INTERVAL '62 minutes' ORDER BY datetime asc LIMIT 1")

      row = cur.fetchone()

      if row == None:

        users_before = current_users
        posts_before = num_posts
        servers_before = num_servers
        fed_users_before = fed_users
        disc_space_before = db_disk_space

      else:

        users_before = row[0]
        posts_before = row[1]
        servers_before = row[2]
        fed_users_before = row[3]
        disc_space_before = row[4]

      # how many posts at the very beginning of the current week
      cur.execute("SELECT DISTINCT ON (datetime) posts, datetime FROM stats WHERE datetime > date_trunc('week', now()::timestamp) ORDER by datetime asc LIMIT 1")

      row = cur.fetchone()

      if row == None:

        posts_begin_week = num_posts

      else:

        posts_begin_week = row[0]

      cur.close()

      users_hour = current_users - users_before
      posts_hour = num_posts - posts_before
      servers_hour = num_servers - servers_before
      fed_users_hour = fed_users - fed_users_before
      inc_disc_space_hour = db_disk_space - disc_space_before

      ### calc average of disk space increase with media_proxy enabled (deactivated on 25/5/19 18:36
      #cur.execute("select sum(disc_space_hour) from stats where datetime > '2019-05-22 15:00:00' and datetime < '2019-05-25 19:00:00'")
      #with_media_proxy_total_MB = cur.fetchone()[0]
      #print with_media_proxy_total_MB
      #average_MB = with_media_proxy_total_MB / 75 # 75 hours running with media_proxy activated
      #print average_MB

      #cur.close()


    except (Exception, psycopg2.DatabaseError) as error:

      print (traceback.format_exc())
      sys.exit(':-(')

    finally:

      if conn is not None:

        conn.close()

    #if num_posts-posts_begin_week == 0:

      #posts_active = 0

    #elif num_posts-posts_begin_week > 0:

      #posts_active =(num_posts-posts_begin_week)/active

    print (" ")
    print ("##################################################")
    print ("# " + pleroma_hostname + " stats" + " - " + str(ara) + " #")
    print ("##################################################")
    print (" ")
    print ("Current users: "+str(current_users))
    print ("Users before: "+str(users_before))
    print ("New users x hour: "+str(users_hour))
    print ("-----------------")
    print ("Federated users: "+str(fed_users))
    print ("Fed users before: "+str(fed_users_before))
    print ("New fed users: "+str(fed_users_hour))
    print ("-----------------")
    print ("Posts: "+str(num_posts))
    print ("Posts before: "+str(posts_before))
    print ("Posts at beginning current week:"+str(posts_begin_week))
    print ("Posts this week:"+str(num_posts-posts_begin_week))
    print ("Posts x hour: "+str(posts_hour))
    print ("Posts per user: %s "% posts_per_user)
    print ("-----------------")
    print ("Federated servers: "+str(num_servers))
    print ("Federated servers before: "+str(servers_before))
    print ("Federating servers x hour: "+str(servers_hour))
    print ("-----------------")
    print ("Unreached servers: " + str(len(hosts_unreached)))
    print ("-----------------")
    print ("Database used disc space (MB): " + str(db_disk_space))
    print ("Database increase last hour (MB): " + str(inc_disc_space_hour))
    print ("-----------------")
    print ("Active users:"+str(active))
    print ("Posts x active users: "+str(posts_active))

  else:

    cur.close()

except (Exception, psycopg2.DatabaseError) as error:

  print (traceback.format_exc())
  sys.exit(':-(')

finally:

  if conn is not None:
      conn.close()

########################################################################################################################################################################################################################
# Connect to Grafana's Postgresql DB pleroma_stats to save all data needed to graph stats
# used columns:
# datetime | users | users_hour | posts | posts_hour | posts_user | interactions | active | active30 | servers | servers_hour | posts_active | federated_users | federated_users_hour | fed_posts_hour | used_disk_space
#----------+---------+-------------+-------+-------------+--------------+--------+-----------+----------+------------+-------------------------------------------------------------------------------------------------

insert_row = """INSERT INTO stats(datetime, users, users_hour, posts, posts_hour, posts_users, interactions, active, active30, servers, servers_hour, posts_active, federated_users, federated_users_hour, fed_posts_hour, used_disk_space, disc_space_hour)
             VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING datetime;"""
conn = None

ara = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

try:

  conn = psycopg2.connect(cstring_grafana)

  cur = conn.cursor()

  # execute INSERT
  cur.execute(insert_row, (ara, current_users, users_hour, num_posts, posts_hour, posts_per_user, interactions, active, active30, num_servers, servers_hour, posts_active, fed_users, fed_users_hour, fed_posts_last_hour, db_disk_space, inc_disc_space_hour))

  # get the id
  datetime = cur.fetchone()[0]

  # commit data
  conn.commit()
  # close the connection
  cur.close()

except (Exception, psycopg2.DatabaseError) as error:

  print(traceback.format_exc())
  sys.exit(':-(')

finally:

  if conn is not None:

    conn.close()

#######################################################################################################################
# Connect to Postgresql DB to fetch users increase in the last hour, last day and last week
########################################################################################################################

try:

  conn = None
  conn = psycopg2.connect(cstring_grafana)

  cur = conn.cursor()

  cur.execute("SELECT DISTINCT ON (datetime) users,datetime FROM stats WHERE datetime > current_timestamp - INTERVAL '62 minutes' ORDER BY datetime asc LIMIT 1")

  row = cur.fetchone()
  users_hour = row[0]

  cur.execute("SELECT DISTINCT ON (datetime) users,datetime FROM stats WHERE datetime > current_timestamp - INTERVAL '25 hours' ORDER BY datetime asc LIMIT 1")

  row = cur.fetchone()
  users_day = row[0]

  cur.execute("SELECT DISTINCT ON (datetime) users,datetime FROM stats WHERE datetime > current_timestamp - INTERVAL '169 hours' ORDER BY datetime asc LIMIT 1")

  row = cur.fetchone()
  users_week = row[0]

  cur.close()

except (Exception, psycopg2.DatabaseError) as error:

  print (traceback.format_exc())
  sys.exit(':-(')

finally:

  if conn is not None:

      conn.close()

####################################################################################

inc_hour = current_users - users_hour
inc_day = current_users - users_day
inc_week = current_users - users_week

print ("New users last hour: "+str(inc_hour))
print ("New users last day: "+str(inc_day))
print ("New users last week: "+str(inc_week))
print ("-----------------------------")
print ("   spla@pleroma.cat @ 2019   ")
print ("-----------------------------")

###################################################################################
