# Followup noticed errors
- missmatched counts on the views
- frontier_queue table is empty

# sitemap
- Correctly parse the sitemaps
- Properly parse and injest the links

# Ensure proper deduping
- should be done on the server at the injestion stage to allow multiple workers

# Injestion
- make sure that injestion isn't a bottle neck, especially duplication hash checking

# Queue optimization

The current queue implementation is inneficient, and doesn't respect the defined "niceness" correctly, all workers wait, but multiple may hit the same ip, and this timeout causes it to happen at the same time
 to avoid this store per ip rate limits and add a choostable strategy options:
- prefered worker:
after a worker fetches a domain store the ip, the timeout and the worker, give it other site's queue requests until the timeout is finished, then queue that one, if multiple, this allows ensuring that the same ip is allways called by the same worker, thus it can chew anything else it gets in the mean time, but this could cause problems with balancing (unless the quzeue also balances the common ips periodically)
- delegate handle
when workers request a dequeue, simply skip timedout candidates and return the best one,  it doesn't guarantee a network delay won't cause the worker to send two in the row, but it's way simpler

I like the simpler delagate handling, aapproach, it should also handle the different ip crawlers, by holding timeots per crawler table, something like

crawler_ip| site_ip | valid_after (or last_ts)
----------------------------------
localhost | somesite.com | <ts>

after the expiration it shouldn't be skipped from the queue but added on the next request and removed from the cooldown table (in memory)

for the ips you have to use actual ips, not the urls

this should automatically give each worker a valid target

>[!Caution] downside:
> there is downtime of waiting on the websocket exchange, although it does simplify it

>[!NOTE]
>The soulution could be to have the workers hold a very small local queue, where all members are guaranteed to be valid at fetch, but that raises the timeout double problem, and aditional complexity

if there is a simple solution that handels this correctly and is a common approach use that one, there may even be an implementation example in the notebooks



# visual

fix the dashboard queue widget isn't there

make 2 versions of the force graph one static for the results and one dynamic, which is directly the current one

the dynamic one should allow "replaying", by just iterating the logs from history

2 view levels, sites, pages of a site (click or zoom level triggered)