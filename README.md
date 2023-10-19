Graphical Rebased's stats with Python + Postgresql + Grafana
============================================================

Python script that gets *realtime* stats data from [Rebased(Pleroma fork)](https://gitlab.com/soapbox-pub/rebased)'s DB.
Thist  script is based on work by FloatingGhost (https://github.com/FloatingGhost/pleroma-stats)

### Dependencies

-   Python3
-   Grafana
-   Postgresql server 

Install python deps:
```bash
sudo pip3 install pipenv
pipenv install
```

### Usage:

1. Edit `config.txt` to specify the hostname of the Rebased server you would like to get data from, its DB 
   name and DB user and also the DB name and DB user for Grafana.

2. Create one Postgresql database for Grafana, in example 'pleroma_stats', with two tables. We change data type for disk usage (from integer to float) and add a function and a trigger for delete rows older than 2 days, because the storical data are stored in Prometheus. If yuo want to use directly PostgreSQL with your Grafana simply not create this functione and this trigger:

```sql
CREATE DATABASE pleroma_stats WITH OWNER pleroma;
\c pleroma_stats;
CREATE TABLE stats(
DATETIME TIMESTAMPTZ PRIMARY KEY NOT NULL,
USERS INT,
USERS_HOUR INT,
POSTS INT,
POSTS_HOUR INT, POSTS_USERS INT,
INTERACTIONS INT,
ACTIVE INT, ACTIVE30 INT,
SERVERS INT, SERVERS_HOUR INT,
POSTS_ACTIVE INT,
FEDERATED_USERS INT, FEDERATED_USERS_HOUR INT,
FED_POSTS_HOUR INT, USED_DISK_SPACE FLOAT4,
DISC_SPACE_HOUR FLOAT4
);

CREATE TABLE unreached_servers(
SERVER VARCHAR(30),
SINCE TIMESTAMP,
DAYS VARCHAR(30),
INSERTED_AT TIMESTAMP PRIMARY KEY NOT NULL,
DATETIME TIMESTAMPTZ
);

CREATE OR REPLACE FUNCTION public.delete_old_rows()
 RETURNS trigger
 LANGUAGE plpgsql
AS $function$
BEGIN
  DELETE FROM pleroma_stats.public.stats WHERE datetime < CURRENT_TIMESTAMP - INTERVAL '2 days';
  RETURN NULL;
END;
$function$
;

CREATE TRIGGER trigger_delete_old_rows
    AFTER INSERT ON pleroma_stats.public.stats
    EXECUTE PROCEDURE delete_old_rows();
```

3. `pipenv run python pleroma-stats.py`
4. Use your favourite scheduling method to set `pleroma-stats.py` to run regularly, using pipenv.
5. Add the datasource PostgreSQL to your Grafana, configuring Host (usually localhost:5432), Database (in the example is pleroma_stats) and User fields. 

Then you could graph your Pleroma server stats with Grafana's PostgreSQL datasource!
It gets all needed data from Pleroma's Postgresql database and then store stats to a new Postgresql database created above, to feed Grafana with their values.

What values can you track in Grafana?

- Local users
- New local users / hour
- Local posts
- Local posts / hour
- Posts per user
- Federated users
- New federated users / hour
- Federated servers
- New federated servers / hour
- Unreached servers (How many & which ones)

* New in v1.2!
- Federated posts / hour 
- Disc space used by the Pleroma's database
- Database disc space increase / hour
