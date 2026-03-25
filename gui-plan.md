Part of this project is to vizualize the reslults of the crawler, although not completed I feel like we should start with this

for any references or unknowns, consider the implementations in the rest of the project, specifically pa/crawler for the crawler implementation and db for the database structure

use .refs directory as a knowledge base for implementation requirements of the entire project, the notebooks usually provide actual intended examples as well

use the following points to construct a comprehensive plan, with todos and thuroughly evaluate each point, on decisions, use the objectively best approach for the usecase, or ask me about design prefereneces and list pros and cons of the options when not resolvable, for style preferences if not specified try to generate something that looks good, try to make it configurable, but don't prompt

inside the new `manager` directory of the repository root create a new dotnet blazor project:
- keep the project as simple as posible for the task it's supposed to just be a very basic dashboard for managing the project runners
- create modular components for anything that would need 
- no need on updating any readmes yet or adding setup/other scripts, for now do it like a completley seperate project and ensure that the current setup keeps working exactly the same
- The structure/architechure should support the following:
  - connection to the databese (ef)
    - migrations exclusevly in db/migrations
    - use tools to construct the model files or just setup models for the tables needed I guess (database fiost)
    - use repo root docker-compose as credential source of truth
  - vizualisation of the results
    - D3.js
    - Graph:
      - node = page
      - edge = link
    - make it interactable (tooltips, click, pan, zoom, etc)
    - nodes that have more incomming edges should be drawn bigger
    - export svg and or pdf vector image
    - if possible make the node graph searchable
    - if possible create a "live view that shows what workers are doing", maybe even vizualize the actual worker active positions, their search stack
    - if possible a "replay" feature to show how each worker navigated
  - a basic searchable list of the results (fuzzy preferably), show relevant info, like page type, etc
  - statistics:
    - number of pages
    - number of duplicates
    - number of images
    - binary file types
  - crawler manager (this part is missing a lot of implementation so just prepare the gui and whatever possible)
    - list of crawlers
    - status, next, etc
    - spawn new crawlers (instances/threads locally, probably dockarized nodes)
    - kill or pause existing ones
    - track: errors, dead links, spider traps
    - setup parameters, seed domains, etc
    - search strategy select and paremetarize (some way to allow multiple, and configure them per worker/for all workers)
    - proxy list
    - way to synchronize the
  - worker api
    - the 'manager' blazor should also be able to act as a server (aspnet or whatever is preferd in blazor when not using)
    - crawlers reporting using http rest/websockets whichever seems better
    - provide seeds, and strategy adjustments (let's say 2 workers hit the same domain, they should be informed of each other and only one may continue)
    - some form of authentication and setup script generation for workers (like docker script + token, or just token, and the worker will have to config it's target server and the auth token)
  - don't do user gui auth yet but keep it as a possibility in the future (setup required structure)
  - whatever else I missed that may be required by the project specs
- web interface layout
  - the gui should be a simple blazor app
  - it should have a admin app type layout
  - create css styling for the app, do a modern minimalist and flat styles, take inspiration from gitlab main light theme in the version 18.9, generate the theme colors as well but make them all configurable in one place (like with bootstrap), keep the styling consistant and centralized as much as possible
    - the icon buttons should be mostly borderless, unless there is a specific reason
    - use mdi icons library for the icons, and create icon button reusable component
    - use icons whenever possible, use them from the mdi icon library mentioned above
  - the web interface should contain the following pages
    - dashboard/home
      - should contain statististics, 
      - the main vizualization node graph (with a "fullscreen button" or maybe a "open full" and change to another page dedicated to the graph entirely)
      - worker status
        - piechart (how many active, idle, done, error, etc, click on a status, should filter the list below)
        - small scrollable and serchable list, with quick indicators
      - server resource status
    - workers
      - full worker list, with statues and data, and on hover show options to stop, pause, delete, duplicate/multiply
      - a "add worker" button for the outside worker setup
      - "add workers" button, which shows options to spawn them locally or the api token way
        - the setup workers, should be it's own dedicated page, with all configurable parameters, and settings the workers may need
        - the first iteration should just dumb instance the crawlers locally in threads or via python's multiprocessing whichever is easier
      - setup shared search strategies, and synchronization rules or something in that vein
      - view worker logs
      - view all worker settings (and edit them)
      - apply settings to multiple workers at once or to all of them
      - maybe some way to group workers and set groupwise settings
    - node graph
      - search
      - zoom,pan,move
      - filters by type, etc
      - full page with sidebar
      - click shows link and additional page details, selects it
        - allows options like show connected, and gray out unconnected nodes
      - find some standard or logical way to group them at different zoom levels
    - collected page results
      - Indepth search parameters, filters, etc.
      - "show on graph" -> open graph with the node in the center 
      - page details, etc.
    - anything else maybe needed

since blazor uses websockets or singnalr or whatever, use this as an advantage to show the data completley live whenever possible, keep the live nature as a consideration with the style desingns


For now setup gui layout, components, elements, etc., the database connection, and whatever is possible with our current implementation, but mark the elements that need to be implemented on the python side here as well, and document the parts that need to be implemented/finished/changed to implement the feature, although try to plan the gui in a way that it will work with placeholders, so it will not have to change later much

create a coherant step by step implementation plan to follow when implementing this module, to be copilot optimized. reference the code, the instructions and this file whenever needed, document implemented steps, and requirements for the ones that cannot be done (what crawler code needs to have implemented and how), prioritize the parts that are listed in instructions.md and the ones that the worker implementation already supports, but plan ahead for the missing parts and implement the parts you can.