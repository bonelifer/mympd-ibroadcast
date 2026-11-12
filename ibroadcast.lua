-- {"name":"iBroadcast","file":"","version":0,"order":5,"arguments":[]}
local service = "http://127.0.0.1:18769"
local args = mympd_arguments

local function valid_id(id)
    return type(id) == "string" and id:match("^%d+$") ~= nil
end

local function encode(value)
    return value:gsub("([^%w_.~-])", function(char)
        return string.format("%%%02X", string.byte(char))
    end)
end

local function request(path, payload)
    local method = payload and "POST" or "GET"
    local headers = payload and "Content-Type: application/json\r\n" or ""
    local rc, status, _, body = mympd.http_client(
        method, service .. path, headers, payload and json.encode(payload) or "", false
    )
    if rc == 0 and status == 200 then
        return json.decode(body)
    end
end

local function choose(title, field, entries, context)
    if not entries then
        return "Could not load the iBroadcast library"
    end
    if #entries == 0 then
        return "No iBroadcast entries found"
    end
    local values, labels = {}, {}
    for _, entry in ipairs(entries) do
        values[#values + 1] = entry.id
        labels[#labels + 1] = entry.name
    end
    local fields = {{
        name = field, type = "select", value = values,
        displayValue = labels, defaultValue = values[1]
    }}
    if context then
        fields[2] = {name = "page_context", type = "hidden", value = context}
    end
    return mympd.dialog(title, fields, "iBroadcast")
end

local function track_uris(ids)
    local uris = {}
    for _, id in ipairs(ids) do
        if not valid_id(id) then
            return nil
        end
        uris[#uris + 1] = service .. "/stream/" .. id
    end
    return uris
end

local function queue(ids, play)
    local uris = track_uris(ids)
    if not uris or #uris == 0 then
        return "No iBroadcast tracks found"
    end
    local rc = mympd.api("MYMPD_API_QUEUE_APPEND_URIS", {uris = uris, play = play ~= false})
    return rc == 0 and "Queued iBroadcast music" or "Could not queue iBroadcast music"
end

local function browse(mode, value)
    return request("/browse?mode=" .. mode .. "&value=" .. encode(value or ""))
end

local function ids_from(entries)
    local ids = {}
    for _, entry in ipairs(entries or {}) do
        ids[#ids + 1] = entry.id
    end
    return ids
end

local function save_playlist()
    local name, count, destination = args.playlist_name, tonumber(args.count), args.destination
    name = type(name) == "string" and name:match("^%s*(.-)%s*$") or ""
    if #name > 100 or name == ""
        or name:find("[%c/\\]") or name == "." or name == ".."
        or not count or count < 1 or count > 100 or count % 1 ~= 0
        or (destination ~= "mpd" and destination ~= "ibroadcast" and destination ~= "both") then
        return "Enter a playlist name and a song count from 1 to 100"
    end
    local ids = ids_from(browse("random", tostring(count)))
    if #ids == 0 then
        return "Could not load random iBroadcast tracks"
    end
    if destination ~= "ibroadcast" then
        local temporary = "iBroadcast-temp-" .. os.time() .. "-" .. math.random(100000000, 999999999)
        local rc = mympd.api("MYMPD_API_PLAYLIST_CONTENT_APPEND_URIS", {
            plist = temporary, uris = track_uris(ids)
        })
        if rc == 0 then
            rc = mympd.api("MYMPD_API_PLAYLIST_RENAME", {plist = temporary, newName = name})
        end
        if rc ~= 0 then
            mympd.api("MYMPD_API_PLAYLIST_RM", {plists = {temporary}})
            return "Could not create the MPD playlist. Check whether the name is already used."
        end
    end
    if destination ~= "mpd" and not request("/playlists", {name = name, ids = ids}) then
        if destination == "both" then
            return "Saved to MPD. iBroadcast did not confirm the save. Check iBroadcast before retrying."
        end
        return "iBroadcast did not confirm the save. Check the library and write permission before retrying."
    end
    local label = ({mpd = "MPD", ibroadcast = "iBroadcast", both = "MPD and iBroadcast"})[destination]
    return "Saved " .. #ids .. " songs to " .. label
end

local groups = {genre = true, year = true, tag = true, playlist = true}
local rankings = {recent = true, rated = true, played = true}

local function browse_path(mode, value)
    return "/browse?mode=" .. mode .. "&value=" .. encode(value or "")
end

local function show_page(mode, value, offset)
    local path, field, title
    local group_list = groups[mode] and value == ""
    if mode == "artists" or mode == "albums" then
        path = "/" .. mode
        if mode == "albums" and value ~= "" then
            if not valid_id(value) then return "Invalid iBroadcast artist" end
            path = path .. "/" .. value
        end
        field = mode == "artists" and "artist" or "album"
        title = "iBroadcast " .. mode
    elseif mode == "tracks" and valid_id(value) then
        path, field, title = "/tracks/" .. value, "track", "iBroadcast tracks"
    elseif groups[mode] or rankings[mode] or mode == "search" then
        path = browse_path(mode, value)
        field = group_list and "group" or (mode == "search" and "search_result" or "track")
        title = mode == "search" and "Search iBroadcast" or "Browse iBroadcast"
    else
        return "Invalid iBroadcast page"
    end
    local separator = path:find("?", 1, true) and "&" or "?"
    local page = request(path .. separator .. "offset=" .. offset)
    if not page or #page.entries == 0 then
        return choose(title, field, page and page.entries)
    end
    local entries = page.entries
    if group_list then
        for _, entry in ipairs(entries) do
            entry.id = mode .. ":" .. entry.id
        end
    elseif mode == "tracks" then
        table.insert(entries, 1, {id = "album:" .. value, name = "Play whole album"})
    elseif groups[mode] then
        local action = mode == "playlist" and "whole" or "shuffle"
        local label = mode == "playlist" and "Play whole playlist" or "Shuffle this selection"
        table.insert(entries, 1, {id = action .. ":" .. mode .. ":" .. value, name = label})
    end
    for _, direction in ipairs({"previous", "next"}) do
        if page[direction] then
            entries[#entries + 1] = {
                id = "page:" .. page[direction],
                name = direction == "previous" and "Previous page" or "Next page"
            }
        end
    end
    return choose(title, field, entries, mode .. ":" .. value)
end

if type(args.page_context) == "string" then
    for _, field in ipairs({"artist", "album", "track", "group", "search_result"}) do
        local selected = args[field]
        local offset = type(selected) == "string" and selected:match("^page:(%d+)$")
        if offset then
            local mode, value = args.page_context:match("^(%a+):(.*)$")
            return show_page(mode, value, offset)
        end
    end
end

if args.playlist_name ~= nil then
    return save_playlist()
end
if args.query ~= nil then
    if type(args.query) ~= "string" or not args.query:match("%S") or #args.query > 100 then
        return "Enter a search term up to 100 characters"
    end
    return show_page("search", args.query, 0)
end
if type(args.search_result) == "string" then
    local kind, id = args.search_result:match("^(%a+):(%d+)$")
    if kind == "tracks" then
        return queue({id})
    elseif kind == "artists" then
        return show_page("albums", id, 0)
    elseif kind == "albums" then
        return show_page("tracks", id, 0)
    end
    return "Invalid iBroadcast search result"
end
if type(args.group) == "string" then
    local mode, value = args.group:match("^(%a+):(.+)$")
    if not groups[mode] or not value then return "Invalid iBroadcast selection" end
    return show_page(mode, value, 0)
end

if args.track then
    if valid_id(args.track) then
        return queue({args.track})
    end
    local action, mode, value
    if type(args.track) == "string" then
        action, mode, value = args.track:match("^(%a+):(%a+):(.+)$")
    end
    if action == "whole" or action == "shuffle" then
        local ids = ids_from(groups[mode] and browse(mode, value))
        if action == "shuffle" then
            for i = #ids, 2, -1 do
                local j = math.random(i)
                ids[i], ids[j] = ids[j], ids[i]
            end
        end
        return queue(ids)
    end
    local album = type(args.track) == "string" and args.track:match("^album:(%d+)$")
    if not album then
        return "Invalid iBroadcast selection"
    end
    local tracks = request("/tracks/" .. album)
    if not tracks then
        return "Could not load iBroadcast tracks"
    end
    local ids = {}
    for _, track in ipairs(tracks) do
        ids[#ids + 1] = track.id
    end
    return queue(ids)
end

if args.album then
    if not valid_id(args.album) then
        return "Invalid iBroadcast album"
    end
    return show_page("tracks", args.album, 0)
end

if args.artist then
    if not valid_id(args.artist) then
        return "Invalid iBroadcast artist"
    end
    return show_page("albums", args.artist, 0)
end

local mode = args.browse
if mode == "albums" or mode == "artists" then
    return show_page(mode, "", 0)
elseif mode == "random_one" or mode == "random_ten" then
    local play = mode == "random_one"
    local tracks = browse("random", play and "1" or "10")
    if not tracks then
        return "Could not load random iBroadcast tracks"
    end
    return queue(ids_from(tracks), play)
elseif mode == "search" then
    return mympd.dialog("Search iBroadcast", {{
        name = "query", type = "text", value = ""
    }}, "iBroadcast")
elseif mode == "create" then
    return mympd.dialog("Create a random playlist", {
        {name = "playlist_name", type = "text", value = ""},
        {name = "count", type = "text", value = "25"},
        {name = "destination", type = "select", value = {"both", "mpd", "ibroadcast"},
            displayValue = {"MPD and iBroadcast", "MPD", "iBroadcast"}, defaultValue = "both"}
    }, "iBroadcast")
elseif groups[mode] or rankings[mode] then
    return show_page(mode, "", 0)
end
local tags = request("/browse?mode=tag&offset=0")
local no_tags = tags and tags.total == 0
return choose("Browse iBroadcast", "browse", {
    {id = "artists", name = "Artists"}, {id = "albums", name = "Albums"},
    {id = "playlist", name = "Saved playlists"}, {id = "search", name = "Search"},
    {id = "genre", name = "Genres"}, {id = "year", name = "Years"},
    {id = no_tags and "tag_unavailable" or "tag",
        name = no_tags and "Tags (none in library)" or "Tags"},
    {id = "recent", name = "Recently uploaded"},
    {id = "rated", name = "Top rated"}, {id = "played", name = "Most played"},
    {id = "random_one", name = "Play one random song"},
    {id = "random_ten", name = "Queue 10 random songs"},
    {id = "create", name = "Create a random playlist"}
})
