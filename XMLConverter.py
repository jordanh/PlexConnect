#!/usr/bin/env python

"""
Sources:

ElementTree
http://docs.python.org/2/library/xml.etree.elementtree.html#xml.etree.ElementTree.SubElement

trailers.apple.com root URL
http://trailers.apple.com/appletv/us/js/application.js
navigation pane
http://trailers.apple.com/appletv/us/nav.xml
->top trailers: http://trailers.apple.com/appletv/us/index.xml
->calendar:     http://trailers.apple.com/appletv/us/calendar.xml
->browse:       http://trailers.apple.com/appletv/us/browse.xml
"""


import os
import sys
import traceback
import inspect 
import string, random
import copy  # deepcopy()

try:
    import xml.etree.cElementTree as etree
except ImportError:
    import xml.etree.ElementTree as etree

import time, uuid, hmac, hashlib, base64
from urllib import quote_plus, unquote_plus
import urllib2
import urlparse

from Version import __VERSION__  # for {{EVAL()}}, display in settings page
import Settings, ATVSettings
import PlexAPI
from Debug import *  # dprint(), prettyXML()
import Localize

import PILBackgrounds
from PILBackgrounds import isPILinstalled

g_param = {}
def setParams(param):
    global g_param
    g_param = param

g_ATVSettings = None
def setATVSettings(cfg):
    global g_ATVSettings
    g_ATVSettings = cfg



"""
# aTV XML ErrorMessage - hardcoded XML File
"""
def XML_Error(title, desc):
    errorXML = '\
<?xml version="1.0" encoding="UTF-8"?>\n\
<atv>\n\
    <body>\n\
        <dialog id="com.sample.error-dialog">\n\
            <title>' + title + '</title>\n\
            <description>' + desc + '</description>\n\
        </dialog>\n\
    </body>\n\
</atv>\n\
';
    return errorXML



def XML_PlayVideo_ChannelsV1(baseURL, path):
    XML = '\
<atv>\n\
  <body>\n\
    <videoPlayer id="com.sample.video-player">\n\
      <httpFileVideoAsset id="' + path + '">\n\
        <mediaURL>' + baseURL + path + '</mediaURL>\n\
        <title>*title*</title>\n\
        <!--bookmarkTime>{{EVAL(int({{VAL(Video/viewOffset:0)}}/1000))}}</bookmarkTime-->\n\
        <videoPlayerSettings>\n\
          <!-- PMS, OSD settings, ... -->\n\
          <baseURL>' + baseURL + '</baseURL>\n\
          <accessToken></accessToken>\n\
          <showClock>False</showClock>\n\
          <timeFormat></timeFormat>\n\
          <clockPosition></clockPosition>\n\
          <overscanAdjust></overscanAdjust>\n\
          <showEndtime>False</showEndtime>\n\
          <subtitleSize></subtitleSize>\n\
        </videoPlayerSettings>\n\
        <myMetadata>\n\
          <key></key>\n\
          <ratingKey></ratingKey>\n\
          <duration></duration>\n\
          <subtitleURL></subtitleURL>\n\
        </myMetadata>\n\
      </httpFileVideoAsset>\n\
    </videoPlayer>\n\
  </body>\n\
</atv>\n\
';
    dprint(__name__,2 , XML)
    return XML



"""
global list of known aTVs - to look up UDID by IP if needed

parameters:
    udid - from options['PlexConnectUDID']
    ip - from client_address btw options['aTVAddress']
"""
g_ATVList = {}

def declareATV(udid, ip):
    global g_ATVList
    if udid in g_ATVList:
        g_ATVList[udid]['ip'] = ip
    else:
        g_ATVList[udid] = {'ip': ip}

def getATVFromIP(ip):
    # find aTV by IP, return UDID
    for udid in g_ATVList:
        if ip==g_ATVList[udid].get('ip', None):
            return udid
    return None  # IP not found



"""
# XML converter functions
# - translate aTV request and send to PMS
# - receive reply from PMS
# - select XML template
# - translate to aTV XML
"""
def XML_PMS2aTV(PMS_address, path, options):
    # double check aTV UDID, redo from client IP if needed/possible
    if not 'PlexConnectUDID' in options:
        UDID = getATVFromIP(options['aTVAddress'])
        if UDID:
            options['PlexConnectUDID'] = UDID
        else:
            # aTV unidentified, UDID not known    
            return XML_Error('PlexConnect','Unexpected error - unidentified ATV')
    else:
        declareATV(options['PlexConnectUDID'], options['aTVAddress'])  # update with latest info
    
    UDID = options['PlexConnectUDID']
    
    # determine PMS_uuid, PMSBaseURL from IP (PMS_mark)
    PMS_uuid = PlexAPI.getPMSFromAddress(UDID, PMS_address)
    PMS_baseURL = PlexAPI.getPMSProperty(UDID, PMS_uuid, 'baseURL')
    
    # check cmd to work on
    cmd = ''
    channelsearchURL = ''
    if 'PlexConnect' in options:
        cmd = options['PlexConnect']
    
    if 'PlexConnectChannelsSearch' in options:
        channelsearchURL = options['PlexConnectChannelsSearch'].replace('+amp+', '&')
     
    dprint(__name__, 1, "PlexConnect Cmd: " + cmd)
    dprint(__name__, 1, "PlexConnectChannelsSearch: " + channelsearchURL)
    
    # check aTV language setting
    if not 'aTVLanguage' in options:
        dprint(__name__, 1, "no aTVLanguage - pick en")
        options['aTVLanguage'] = 'en'
    
    # XML Template selector
    # - PlexConnect command
    # - path
    # - PMS ViewGroup
    XMLtemplate = ''
    PMS = None
    PMSroot = None
    
    # XML direct request or
    # XMLtemplate defined by solely PlexConnect Cmd
    if path.endswith(".xml"):
        XMLtemplate = path.lstrip('/')
        path = ''  # clear path - we don't need PMS-XML
    
    elif cmd=='ChannelsSearch':
        XMLtemplate = 'ChannelsSearch.xml'
        path = ''
        
    elif cmd.startswith('Play:'):
        opt = cmd[len('Play:'):]  # cut command:
        parts = opt.split(':',1)
        if len(parts)==2:
            options['PlexConnectPlayType'] = parts[0]  # Single, Continuous # decoded in PlayVideo.xml, COPY_PLAYLIST
            options['PlexConnectRatingKey'] = parts[1]  # ratingKey # decoded in PlayVideo.xml
        else:
            return XML_Error('PlexConnect','Unexpected "Play" command syntax')
        XMLtemplate = 'PlayVideo.xml'
    
    elif cmd.startswith('PlayAudio'):  # PlayAudio: or PlayAudio_plist:
        parts = cmd.split(':',3)
        if len(parts)==4:
            XMLtemplate = parts[0] + '.xml'
            options['PlexConnectPlayType'] = parts[1]  # Single, Continuous # decoded in PlayAudio.xml
            options['PlexConnectRatingKey'] = parts[2]  # ratingKey
            options['PlexConnectCopyIx'] = parts[3]  # copy_ix
        else:
            return XML_Error('PlexConnect','Unexpected "PlayAudio" command syntax')
    
    elif cmd=='PlayVideo_ChannelsV1':
        dprint(__name__, 1, "playing Channels XML Version 1: {0}".format(path))
        auth_token = PlexAPI.getPMSProperty(UDID, PMS_uuid, 'accesstoken')
        path = PlexAPI.getDirectVideoPath(path, auth_token)
        return XML_PlayVideo_ChannelsV1(PMS_baseURL, path)  # direct link, no PMS XML available
    
    elif cmd=='PlayTrailer':
        trailerID = options['PlexConnectTrailerID']
        info = urllib2.urlopen("http://youtube.com/get_video_info?video_id=" + trailerID).read()
        parsed = urlparse.parse_qs(info)
        
        key = 'url_encoded_fmt_stream_map'
        if not key in parsed:
            return XML_Error('PlexConnect', 'Youtube: No Trailer Info available')
        streams = parsed[key][0].split(',')
        
        url = ''
        for i in range(len(streams)):
            stream = urlparse.parse_qs(streams[i])
            if stream['itag'][0] == '18':
                url = stream['url'][0]
        if url == '':
            return XML_Error('PlexConnect','Youtube: ATV compatible Trailer not available')
        
        return XML_PlayVideo_ChannelsV1('', url.replace('&','&amp;'))

    elif cmd=='Plex_Video_Files_Scanner':
        XMLtemplate = 'HomeVideoSectionTopLevel.xml'

    elif cmd=='Plex_Movie_Scanner':
        XMLtemplate = 'MovieSectionTopLevel.xml'
    
    elif cmd=='Plex_Series_Scanner':
        XMLtemplate = 'TVSectionTopLevel.xml'
        
    elif cmd=='Plex_Photo_Scanner':
        XMLtemplate = 'PhotoSectionTopLevel.xml'

    elif cmd=='Plex_Music_Scanner':
        XMLtemplate = 'MusicSectionTopLevel.xml'
    
    elif cmd=='ScrobbleMenu':
        XMLtemplate = 'ScrobbleMenu.xml'

    elif cmd=='ScrobbleMenuVideo':
        XMLtemplate = 'ScrobbleMenuVideo.xml'

    elif cmd=='ScrobbleMenuTVOnDeck':
        XMLtemplate = 'ScrobbleMenuTVOnDeck.xml'
        
    elif cmd=='ChangeShowArtwork':
        XMLtemplate = 'ChangeShowArtwork.xml'

    elif cmd=='ChangeSingleArtwork':
        XMLtemplate = 'ChangeSingleArtwork.xml'

    elif cmd=='ChangeSingleArtworkVideo':
        XMLtemplate = 'ChangeSingleArtworkVideo.xml'

    elif cmd=='ChangeFanart':
        XMLtemplate = 'ChangeFanArt.xml'
        
    elif cmd=='ChangeFanartVideo':
        XMLtemplate = 'ChangeFanArtVideo.xml'
        
    elif cmd=='PhotoBrowser':
        XMLtemplate = 'Photo_Browser.xml'
        
    elif cmd=='MoviePreview':
        XMLtemplate = 'MoviePreview.xml'
    
    elif cmd=='HomeVideoPrePlay':
        XMLtemplate = 'HomeVideoPrePlay.xml'
        
    elif cmd=='MoviePrePlay':
        XMLtemplate = "MoviePrePlay.xml"
        if g_ATVSettings.getSetting(options['PlexConnectUDID'], 'moviefanart') == 'Show' and \
           isPILinstalled() and \
           options['aTVFirmwareVersion'] >= '6.0':  # watch out: this will make trouble with iOS10
            XMLtemplate = 'MoviePrePlay_Fanart.xml'

    elif cmd=='EpisodePrePlay':
        XMLtemplate = 'EpisodePrePlay.xml'
        if g_ATVSettings.getSetting(options['PlexConnectUDID'], 'tvshowfanart') == 'Show' and \
           isPILinstalled() and \
           options['aTVFirmwareVersion'] >= '6.0':  # watch out: this will make trouble with iOS10
            XMLtemplate = 'EpisodePrePlay_Fanart.xml'
        
    elif cmd=='ChannelPrePlay':
        XMLtemplate = 'ChannelPrePlay.xml'
    
    elif cmd=='ChannelsVideo':
        XMLtemplate = 'ChannelsVideo.xml'

    elif cmd=='ShowByFolder':
        XMLtemplate = 'ShowByFolder.xml'

    elif cmd=='HomeVideoByFolder':
        XMLtemplate = 'HomeVideoByFolder.xml'

    elif cmd == 'HomeVideoDirectory':
        XMLtemplate = 'HomeVideoDirectory.xml'

    elif cmd=='MovieByFolder':
        XMLtemplate = 'MovieByFolder.xml'

    elif cmd == 'MovieDirectory':
        XMLtemplate = 'MovieDirectory.xml'

    elif cmd == 'MovieSection':
        XMLtemplate = 'MovieSection.xml'
    
    elif cmd == 'HomeVideoSection':
        XMLtemplate = 'HomeVideoSection.xml'
        
    elif cmd == 'TVSection':
        XMLtemplate = 'TVSection.xml'
    
    elif cmd.find('SectionPreview') != -1:
        XMLtemplate = cmd + '.xml'
    
    elif cmd == 'AllMovies':
        XMLtemplate = 'Movie_'+g_ATVSettings.getSetting(options['PlexConnectUDID'], 'movieview').replace(' ','')+'.xml'  
    
    elif cmd == 'AllHomeVideos':
        XMLtemplate = 'HomeVideo_'+g_ATVSettings.getSetting(options['PlexConnectUDID'], 'homevideoview').replace(' ','')+'.xml'  
        
    elif cmd == 'MovieSecondary':
        XMLtemplate = 'MovieSecondary.xml'
    
    elif cmd == 'AllShows':
        XMLtemplate = 'Show_'+g_ATVSettings.getSetting(options['PlexConnectUDID'], 'showview').replace(' ','')+'.xml'  
          
    elif cmd == 'TVSecondary':
        XMLtemplate = 'TVSecondary.xml'
        
    elif cmd == 'PhotoSecondary':
        XMLtemplate = 'PhotoSecondary.xml'
        
    elif cmd == 'MusicSecondary':
        XMLtemplate = 'MusicSecondary.xml'
        
    elif cmd == 'Directory':
        XMLtemplate = 'Directory.xml'
    
    elif cmd == 'DirectoryWithPreview':
        XMLtemplate = 'DirectoryWithPreview.xml'

    elif cmd == 'DirectoryWithPreviewActors':
        XMLtemplate = 'DirectoryWithPreviewActors.xml'
    
    elif cmd=='Playlists':
        XMLtemplate = 'Playlists.xml'
    
    elif cmd=='Playlist_Video':
        XMLtemplate = 'Playlist_Video.xml'
    
    elif cmd=='Playlist_Audio':
        XMLtemplate = 'Playlist_Audio.xml'
    
    elif cmd=='Settings':
        XMLtemplate = 'Settings.xml'
        path = ''  # clear path - we don't need PMS-XML
    
    elif cmd=='SettingsVideoOSD':
        XMLtemplate = 'Settings_VideoOSD.xml'
        path = ''  # clear path - we don't need PMS-XML
    
    elif cmd=='SettingsMovies':
        XMLtemplate = 'Settings_Movies.xml'
        path = ''  # clear path - we don't need PMS-XML
        
    elif cmd=='SettingsTVShows':
        XMLtemplate = 'Settings_TVShows.xml'
        path = ''  # clear path - we don't need PMS-XML
 
    elif cmd=='SettingsHomeVideos':
        XMLtemplate = 'Settings_HomeVideos.xml'
        path = ''  # clear path - we don't need PMS-XML

    elif cmd=='SettingsMusic':
        XMLtemplate = 'Settings_Music.xml'
        path = ''  # clear path - we don't need PMS-XML

    elif cmd=='SettingsTopLevel':
        XMLtemplate = 'Settings_TopLevel.xml'
        path = ''  # clear path - we don't need PMS-XML
        
    elif cmd=='SaveSettings':
        g_ATVSettings.saveSettings();
        return XML_Error('PlexConnect', 'SaveSettings!')  # not an error - but aTV won't care anyways.
        
    elif cmd.startswith('SettingsToggle:'):
        opt = cmd[len('SettingsToggle:'):]  # cut command:
        parts = opt.split('+')
        g_ATVSettings.toggleSetting(options['PlexConnectUDID'], parts[0].lower())
        XMLtemplate = parts[1] + ".xml"
        dprint(__name__, 2, "ATVSettings->Toggle: {0} in template: {1}", parts[0], parts[1])
        
        path = ''  # clear path - we don't need PMS-XML
        
    elif cmd==('MyPlexLogin'):
        dprint(__name__, 2, "MyPlex->Logging In...")
        if not 'PlexConnectCredentials' in options:
            return XML_Error('PlexConnect', 'MyPlex Sign In called without Credentials.')
        
        parts = options['PlexConnectCredentials'].split(':',1)        
        (username, auth_token) = PlexAPI.MyPlexSignIn(parts[0], parts[1], options)
        
        g_ATVSettings.setSetting(UDID, 'myplex_user', username)
        g_ATVSettings.setSetting(UDID, 'myplex_auth', auth_token)
        
        XMLtemplate = 'Settings.xml'
        path = ''  # clear path - we don't need PMS-XML
    
    elif cmd=='MyPlexLogout':
        dprint(__name__, 2, "MyPlex->Logging Out...")
        
        auth_token = g_ATVSettings.getSetting(UDID, 'myplex_auth')
        PlexAPI.MyPlexSignOut(auth_token)
        
        g_ATVSettings.setSetting(UDID, 'myplex_user', '')
        g_ATVSettings.setSetting(UDID, 'myplex_auth', '')
        
        XMLtemplate = 'Settings.xml'
        path = ''  # clear path - we don't need PMS-XML
    
    elif cmd.startswith('Discover'):
        auth_token = g_ATVSettings.getSetting(UDID, 'myplex_auth')
        PlexAPI.discoverPMS(UDID, g_param['CSettings'], auth_token)
        
        return XML_Error('PlexConnect', 'Discover!')  # not an error - but aTV won't care anyways.
    
    elif path.startswith('/search?'):
        XMLtemplate = 'Search_Results.xml'
    
    elif path.find('serviceSearch') != -1 or (path.find('video') != -1 and path.lower().find('search') != -1):
        XMLtemplate = 'ChannelsVideoSearchResults.xml'
    
    elif path.find('SearchResults') != -1:
        XMLtemplate = 'ChannelsVideoSearchResults.xml'
        
    elif path=='/library/sections':  # from PlexConnect.xml -> for //local, //myplex
        XMLtemplate = 'Library.xml'
    
    elif path=='/channels/all':
        XMLtemplate = 'Channel_'+g_ATVSettings.getSetting(options['PlexConnectUDID'], 'channelview')+'.xml'
        path = ''
    
    # request PMS XML
    if not path=='':
        if PMS_address[0].isalpha():  # owned, shared
            type = PMS_address
            PMS = PlexAPI.getXMLFromMultiplePMS(UDID, path, type, options)
        else:  # IP
            auth_token = PlexAPI.getPMSProperty(UDID, PMS_uuid, 'accesstoken')
            PMS = PlexAPI.getXMLFromPMS(PMS_baseURL, path, options, authtoken=auth_token)
        
        if PMS==False:
            return XML_Error('PlexConnect', 'No Response from Plex Media Server')
        
        PMSroot = PMS.getroot()
        
        dprint(__name__, 1, "viewGroup: "+PMSroot.get('viewGroup','None'))
    
    # XMLtemplate defined by PMS XML content
    if path=='':
        pass  # nothing to load
    
    elif not XMLtemplate=='':
        pass  # template already selected
    
    elif PMSroot.get('viewGroup','')=='show':
        if PMSroot.get('title2')=='By Folder':
          # By Folder View
          XMLtemplate = 'ShowByFolder.xml'
        else:
          # TV Show grid view
          XMLtemplate = 'Show_'+g_ATVSettings.getSetting(options['PlexConnectUDID'], 'showview')+'.xml'
        
    elif PMSroot.get('viewGroup','')=='season':
        # TV Season view
        XMLtemplate = 'Season_'+g_ATVSettings.getSetting(options['PlexConnectUDID'], 'seasonview')
        if g_ATVSettings.getSetting(options['PlexConnectUDID'], 'tvshowfanart') == 'Show' and \
           isPILinstalled() and \
           options['aTVFirmwareVersion'] >= '6.0':  # watch out: this will make trouble with iOS10
            XMLtemplate = XMLtemplate + '_Fanart.xml'
        else:
            XMLtemplate = XMLtemplate + '.xml'
    
    elif PMSroot.get('viewGroup','')=='movie' and PMSroot.get('thumb','').find('video') != -1:
        if PMSroot.get('title2')=='By Folder':
          # By Folder View
          XMLtemplate = 'HomeVideoByFolder.xml'
        else:
          # Home Video listing
          XMLtemplate = 'HomeVideo_'+g_ATVSettings.getSetting(options['PlexConnectUDID'], 'homevideoview').replace(' ','')+'.xml'
    
    elif PMSroot.get('viewGroup','')=='movie' and PMSroot.get('thumb','').find('movie') != -1:
        if PMSroot.get('title2')=='By Folder':
          # By Folder View
          XMLtemplate = 'MovieByFolder.xml'
        else:
          # Movie listing
          XMLtemplate = 'Movie_'+g_ATVSettings.getSetting(options['PlexConnectUDID'], 'movieview').replace(' ','')+'.xml'
   
    elif PMSroot.get('viewGroup','')=='episode':
        if PMSroot.get('title2')=='On Deck' or \
           PMSroot.get('title2')=='Recently Viewed Episodes' or \
           PMSroot.get('title2')=='Recently Aired' or \
           PMSroot.get('title2')=='Recently Added':
            # TV On Deck View
            XMLtemplate = 'TV_OnDeck.xml'
        else:
            # TV Episode view
            XMLtemplate = 'Episode.xml' 
            if g_ATVSettings.getSetting(options['PlexConnectUDID'], 'tvshowfanart') == 'Show' and \
               isPILinstalled() and \
               options['aTVFirmwareVersion'] >= '6.0':  # watch out: this will make trouble with iOS10
                XMLtemplate = 'Episode_Fanart.xml'
    
    elif PMSroot.get('viewGroup','')=='photo' or \
       path.startswith('/photos') or \
       PMSroot.find('Photo')!=None:
        if PMSroot.find('Directory')==None:
            # Photos only - directly show
            XMLtemplate = 'Photo_Browser.xml'
        else:
            # Photo listing / directory
            XMLtemplate = 'Photo_Directories.xml'
    
    elif PMSroot.get('viewGroup','')=='track':
        XMLtemplate = "Music_Track.xml"
    
    else:
        XMLtemplate = 'Directory.xml'
    
    dprint(__name__, 1, "XMLTemplate: "+XMLtemplate)

    # get XMLtemplate
    aTVTree = etree.parse(sys.path[0]+'/assets/templates/'+XMLtemplate)
    aTVroot = aTVTree.getroot()
    
    # convert PMS XML to aTV XML using provided XMLtemplate
    CommandCollection = CCommandCollection(options, PMSroot, PMS_address, path)
    XML_ExpandTree(CommandCollection, aTVroot, PMSroot, 'main')
    XML_ExpandAllAttrib(CommandCollection, aTVroot, PMSroot, 'main')
    del CommandCollection
    
    if cmd=='ChannelsSearch':
        for bURL in aTVroot.iter('baseURL'):
            if channelsearchURL.find('?') == -1:
                bURL.text = channelsearchURL + '?query='
            else:
                bURL.text = channelsearchURL + '&query='
                
    dprint(__name__, 1, "====== generated aTV-XML ======")
    dprint(__name__, 1, aTVroot)
    dprint(__name__, 1, "====== aTV-XML finished ======")
    
    return etree.tostring(aTVroot)



def XML_ExpandTree(CommandCollection, elem, src, srcXML):
    # unpack template 'COPY'/'CUT' command in children
    res = False
    while True:
        if list(elem)==[]:  # no sub-elements, stop recursion
            break
        
        for child in elem:
            res = XML_ExpandNode(CommandCollection, elem, child, src, srcXML, 'TEXT')
            if res==True:  # tree modified: restart from 1st elem
                break  # "for child"
            
            # recurse into children
            XML_ExpandTree(CommandCollection, child, src, srcXML)
            
            res = XML_ExpandNode(CommandCollection, elem, child, src, srcXML, 'TAIL')
            if res==True:  # tree modified: restart from 1st elem
                break  # "for child"
        
        if res==False:  # complete tree parsed with no change, stop recursion
            break  # "while True"



def XML_ExpandNode(CommandCollection, elem, child, src, srcXML, text_tail):
    if text_tail=='TEXT':  # read line from text or tail
        line = child.text
    elif text_tail=='TAIL':
        line = child.tail
    else:
        dprint(__name__, 0, "XML_ExpandNode - text_tail badly specified: {0}", text_tail)
        return False
    
    pos = 0
    while line!=None:
        cmd_start = line.find('{{',pos)
        cmd_end   = line.find('}}',pos)
        next_start = line.find('{{',cmd_start+2)
        while next_start!=-1 and next_start<cmd_end:
            cmd_end = line.find('}}',cmd_end+2)
            next_start = line.find('{{',next_start+2)
        if cmd_start==-1 or cmd_end==-1 or cmd_start>cmd_end:
            return False  # tree not touched, line unchanged
        
        dprint(__name__, 2, "XML_ExpandNode: {0}", line)
        
        cmd = line[cmd_start+2:cmd_end]
        if cmd[-1]!=')':
            dprint(__name__, 0, "XML_ExpandNode - closing bracket missing: {0} ", line)
        
        parts = cmd.split('(',1)
        cmd = parts[0]
        param = parts[1].strip(')')  # remove ending bracket
        
        res = False
        if hasattr(CCommandCollection, 'TREE_'+cmd):  # expand tree, work COPY, CUT
            line = line[:cmd_start] + line[cmd_end+2:]  # remove cmd from text and tail
            if text_tail=='TEXT':  
                child.text = line
            elif text_tail=='TAIL':
                child.tail = line
            
            try:
                param = XML_ExpandLine(CommandCollection, src, srcXML, param)  # expand any attributes in the parameter
                res = getattr(CommandCollection, 'TREE_'+cmd)(elem, child, src, srcXML, param)
            except:
                dprint(__name__, 0, "XML_ExpandNode - Error in cmd {0}, line {1}\n{2}", cmd, line, traceback.format_exc())
            
            if res==True:
                return True  # tree modified, node added/removed: restart from 1st elem
        
        elif hasattr(CCommandCollection, 'ATTRIB_'+cmd):  # check other known cmds: VAL, EVAL...
            dprint(__name__, 2, "XML_ExpandNode - Stumbled over {0} in line {1}", cmd, line)
            pos = cmd_end
        else:
            dprint(__name__, 0, "XML_ExpandNode - Found unknown cmd {0} in line {1}", cmd, line)
            line = line[:cmd_start] + "((UNKNOWN:"+cmd+"))" + line[cmd_end+2:]  # mark unknown cmd in text or tail
            if text_tail=='TEXT':
                child.text = line
            elif text_tail=='TAIL':
                child.tail = line
    
    dprint(__name__, 2, "XML_ExpandNode: {0} - done", line)
    return False



def XML_ExpandAllAttrib(CommandCollection, elem, src, srcXML):
    # unpack template commands in elem.text
    line = elem.text
    if line!=None:
        elem.text = XML_ExpandLine(CommandCollection, src, srcXML, line.strip())
    
    # unpack template commands in elem.tail
    line = elem.tail
    if line!=None:
        elem.tail = XML_ExpandLine(CommandCollection, src, srcXML, line.strip())
    
    # unpack template commands in elem.attrib.value
    for attrib in elem.attrib:
        line = elem.get(attrib)
        elem.set(attrib, XML_ExpandLine(CommandCollection, src, srcXML, line.strip()))
    
    # recurse into children
    for el in elem:
        XML_ExpandAllAttrib(CommandCollection, el, src, srcXML)



def XML_ExpandLine(CommandCollection, src, srcXML, line):
    pos = 0
    while True:
        cmd_start = line.find('{{',pos)
        cmd_end   = line.find('}}',pos)
        next_start = line.find('{{',cmd_start+2)
        while next_start!=-1 and next_start<cmd_end:
            cmd_end = line.find('}}',cmd_end+2)
            next_start = line.find('{{',next_start+2)

        if cmd_start==-1 or cmd_end==-1 or cmd_start>cmd_end:
            break;
        
        dprint(__name__, 2, "XML_ExpandLine: {0}", line)
        
        cmd = line[cmd_start+2:cmd_end]
        if cmd[-1]!=')':
            dprint(__name__, 0, "XML_ExpandLine - closing bracket missing: {0} ", line)
        
        parts = cmd.split('(',1)
        cmd = parts[0]
        param = parts[1][:-1]  # remove ending bracket
        
        if hasattr(CCommandCollection, 'ATTRIB_'+cmd):  # expand line, work VAL, EVAL...
            
            try:
                param = XML_ExpandLine(CommandCollection, src, srcXML, param)  # expand any attributes in the parameter
                res = getattr(CommandCollection, 'ATTRIB_'+cmd)(src, srcXML, param)
                line = line[:cmd_start] + res + line[cmd_end+2:]
                pos = cmd_start+len(res)
            except:
                dprint(__name__, 0, "XML_ExpandLine - Error in {0}\n{1}", line, traceback.format_exc())
                line = line[:cmd_start] + "((ERROR:"+cmd+"))" + line[cmd_end+2:]
        
        elif hasattr(CCommandCollection, 'TREE_'+cmd):  # check other known cmds: COPY, CUT
            dprint(__name__, 2, "XML_ExpandLine - stumbled over {0} in line {1}", cmd, line)
            pos = cmd_end
        else:
            dprint(__name__, 0, "XML_ExpandLine - Found unknown cmd {0} in line {1}", cmd, line)
            line = line[:cmd_start] + "((UNKNOWN:"+cmd+"))" + line[cmd_end+2:]    
        
        dprint(__name__, 2, "XML_ExpandLine: {0} - done", line)
    return line



"""
# Command expander classes
# CCommandHelper():
#     base class to the following, provides basic parsing & evaluation functions
# CCommandCollection():
#     cmds to set up sources (ADDXML, VAR)
#     cmds with effect on the tree structure (COPY, CUT) - must be expanded first
#     cmds dealing with single node keys, text, tail only (VAL, EVAL, ADDR_PMS ,...)
"""
class CCommandHelper():
    def __init__(self, options, PMSroot, PMS_address, path):
        self.options = options
        self.PMSroot = {'main': PMSroot}
        self.PMS_address = PMS_address  # default PMS if nothing else specified
        self.path = {'main': path}
        
        self.ATV_udid = options['PlexConnectUDID']
        self.PMS_uuid = PlexAPI.getPMSFromAddress(self.ATV_udid, PMS_address)
        self.PMS_baseURL = PlexAPI.getPMSProperty(self.ATV_udid, self.PMS_uuid, 'baseURL')
        self.variables = {}
    
    # internal helper functions
    def getParam(self, src, param):
        parts = param.split(':',1)
        param = parts[0]
        leftover=''
        if len(parts)>1:
            leftover = parts[1]
        
        param = param.replace('&col;',':')  # colon  # replace XML_template special chars
        param = param.replace('&ocb;','{')  # opening curly brace
        param = param.replace('&ccb;','}')  # closinging curly brace
        
        param = param.replace('&quot;','"')  # replace XML special chars
        param = param.replace('&apos;',"'")
        param = param.replace('&lt;','<')
        param = param.replace('&gt;','>')
        param = param.replace('&amp;','&')  # must be last
        
        dprint(__name__, 2, "CCmds_getParam: {0}, {1}", param, leftover)
        return [param, leftover]
    
    def getKey(self, src, srcXML, param):
        attrib, leftover = self.getParam(src, param)
        default, leftover = self.getParam(src, leftover)
        
        el, srcXML, attrib = self.getBase(src, srcXML, attrib)         
        
        # walk the path if neccessary
        while '/' in attrib and el!=None:
            parts = attrib.split('/',1)
            if parts[0].startswith('#') and attrib[1:] in self.variables:  # internal variable in path
                el = el.find(self.variables[parts[0][1:]])
            elif parts[0].startswith('$'):  # setting
                el = el.find(g_ATVSettings.getSetting(self.ATV_udid, parts[0][1:]))
            elif parts[0].startswith('%'):  # PMS property
                el = el.find(PlexAPI.getPMSProperty(self.ATV_udid, self.PMS_uuid, parts[0][1:]))
            else:
                el = el.find(parts[0])
            attrib = parts[1]
        
        # check element and get attribute
        if attrib.startswith('#') and attrib[1:] in self.variables:  # internal variable
            res = self.variables[attrib[1:]]
            dfltd = False
        elif attrib.startswith('$'):  # setting
            res = g_ATVSettings.getSetting(self.ATV_udid, attrib[1:])
            dfltd = False
        elif attrib.startswith('%'):  # PMS property
            res = PlexAPI.getPMSProperty(self.ATV_udid, self.PMS_uuid, attrib[1:])
            dfltd = False
        elif attrib.startswith('^') and attrib[1:] in self.options:  # aTV property, http request options
            res = self.options[attrib[1:]]
            dfltd = False
        elif el!=None and attrib in el.attrib:
            res = el.get(attrib)
            dfltd = False
        
        else:  # path/attribute not found
            res = default
            dfltd = True
        
        dprint(__name__, 2, "CCmds_getKey: {0},{1},{2}", res, leftover,dfltd)
        return [res,leftover,dfltd]
    
    def getElement(self, src, srcXML, param):
        tag, leftover = self.getParam(src, param)
        
        el, srcXML, tag = self.getBase(src, srcXML, tag)
        
        # walk the path if neccessary
        while len(tag)>0:
            parts = tag.split('/',1)
            el = el.find(parts[0])
            if not '/' in tag or el==None:
                break
            tag = parts[1]
        return [el, leftover]
    
    def getBase(self, src, srcXML, param):
        # get base element
        if param.startswith('@'):  # redirect to additional XML
            parts = param.split('/',1)
            srcXML = parts[0][1:]
            src = self.PMSroot[srcXML]
            leftover=''
            if len(parts)>1:
                leftover = parts[1]
        elif param.startswith('/'):  # start at root
            src = self.PMSroot['main']
            leftover = param[1:]
        else:
            leftover = param
        
        return [src, srcXML, leftover]
    
    def getConversion(self, src, param):
        conv, leftover = self.getParam(src, param)
        
        # build conversion "dictionary"
        convlist = []
        if conv!='':
            parts = conv.split('|')
            for part in parts:
                convstr = part.split('=')
                convlist.append((unquote_plus(convstr[0]), unquote_plus(convstr[1])))
        
        dprint(__name__, 2, "CCmds_getConversion: {0},{1}", convlist, leftover)
        return [convlist, leftover]
    
    def applyConversion(self, val, convlist):
        # apply string conversion            
        if convlist!=[]:
            for part in reversed(sorted(convlist)):
                if val>=part[0]:
                    val = part[1]
                    break
        
        dprint(__name__, 2, "CCmds_applyConversion: {0}", val)
        return val
    
    def applyMath(self, val, math, frmt):
        # apply math function - eval
        try:
            x = eval(val)
            if math!='':
                x = eval(math)
            val = ('{0'+frmt+'}').format(x)
        except:
            dprint(__name__, 0, "CCmds_applyMath: Error in math {0}, frmt {1}\n{2}", math, frmt, traceback.format_exc())
        # apply format specifier
        
        dprint(__name__, 2, "CCmds_applyMath: {0}", val)
        return val
    
    def _(self, msgid):
        return Localize.getTranslation(self.options['aTVLanguage']).ugettext(msgid)



class CCommandCollection(CCommandHelper):
    # XML TREE modifier commands
    # add new commands to this list!
    def TREE_COPY(self, elem, child, src, srcXML, param):
        tag, param_enbl = self.getParam(src, param)

        src, srcXML, tag = self.getBase(src, srcXML, tag)        
        
        # walk the src path if neccessary
        while '/' in tag and src!=None:
            parts = tag.split('/',1)
            src = src.find(parts[0])
            tag = parts[1]
        
        # find index of child in elem - to keep consistent order
        for ix, el in enumerate(list(elem)):
            if el==child:
                break
        
        # duplicate child and add to tree
        cnt = 0
        for elemSRC in src.findall(tag):
            key = 'COPY'
            if param_enbl!='':
                key, leftover, dfltd = self.getKey(elemSRC, srcXML, param_enbl)
                conv, leftover = self.getConversion(elemSRC, leftover)
                if not dfltd:
                    key = self.applyConversion(key, conv)
            
            if key:
                self.PMSroot['copy_'+tag] = elemSRC
                self.variables['copy_ix'] = str(cnt)
                cnt = cnt+1
                el = copy.deepcopy(child)
                XML_ExpandTree(self, el, elemSRC, srcXML)
                XML_ExpandAllAttrib(self, el, elemSRC, srcXML)
                
                if el.tag=='__COPY__':
                    for el_child in list(el):
                        elem.insert(ix, el_child)
                        ix += 1
                else:
                    elem.insert(ix, el)
                    ix += 1
        
        # remove template child
        elem.remove(child)
        return True  # tree modified, nodes updated: restart from 1st elem
    
    #syntax: Video, playType (Single|Continuous), key to match (^PlexConnectRatingKey), ratingKey
    def TREE_COPY_PLAYLIST(self, elem, child, src, srcXML, param):
        tag, leftover  = self.getParam(src, param)
        playType, leftover, dfltd = self.getKey(src, srcXML, leftover)  # Single (default), Continuous
        key, leftover, dfltd = self.getKey(src, srcXML, leftover)
        param_key = leftover
        
        src, srcXML, tag = self.getBase(src, srcXML, tag)
        
        # walk the src path if neccessary
        while '/' in tag and src!=None:
            parts = tag.split('/',1)
            src = src.find(parts[0])
            tag = parts[1]
        
        # find index of child in elem - to keep consistent order
        for ix, el in enumerate(list(elem)):
            if el==child:
                break
        
        # filter elements to copy
        cnt = 0
        copy_enbl = False
        elemsSRC = []
        for elemSRC in src.findall(tag):
            child_key, leftover, dfltd = self.getKey(elemSRC, srcXML, param_key)
            if not key:
                copy_enbl = True                           # copy all
            elif playType == 'Continuous' or playType== 'Shuffle':
                copy_enbl = copy_enbl or (key==child_key)  # [0 0 1 1 1 1]
            else:  # 'Single' (default)
                copy_enbl = (key==child_key)               # [0 0 1 0 0 0]
            
            if copy_enbl:
                elemsSRC.append(elemSRC)
        
        # shuffle elements
        if playType == 'Shuffle':
            if not key:
                random.shuffle(elemsSRC)                   # shuffle all
            else:
                elems = elemsSRC[1:]                       # keep first element fix
                random.shuffle(elems)
                elemsSRC = [elemsSRC[0]] + elems
        
        # duplicate child and add to tree
        cnt = 0
        for elemSRC in elemsSRC:
                self.PMSroot['copy_'+tag] = elemSRC
                self.variables['copy_ix'] = str(cnt)
                cnt = cnt+1
                el = copy.deepcopy(child)
                XML_ExpandTree(self, el, elemSRC, srcXML)
                XML_ExpandAllAttrib(self, el, elemSRC, srcXML)
                
                if el.tag=='__COPY__':
                    for el_child in list(el):
                        elem.insert(ix, el_child)
                        ix += 1
                else:
                    elem.insert(ix, el)
                    ix += 1
        
        # remove template child
        elem.remove(child)
        return True  # tree modified, nodes updated: restart from 1st elem
    
    def TREE_CUT(self, elem, child, src, srcXML, param):
        key, leftover, dfltd = self.getKey(src, srcXML, param)
        conv, leftover = self.getConversion(src, leftover)
        if not dfltd:
            key = self.applyConversion(key, conv)
        if key:
            elem.remove(child)
            return True  # tree modified, node removed: restart from 1st elem
        else:
            return False  # tree unchanged
    
    def TREE_ADDXML(self, elem, child, src, srcXML, param):
        tag, leftover = self.getParam(src, param)
        key, leftover, dfltd = self.getKey(src, srcXML, leftover)
        
        PMS_address = self.PMS_address
        
        if key.startswith('//'):  # local servers signature
            pathstart = key.find('/',3)
            PMS_address= key[:pathstart]
            path = key[pathstart:]
        elif key.startswith('/'):  # internal full path.
            path = key
        #elif key.startswith('http://'):  # external address
        #    path = key
        elif key == '':  # internal path
            path = self.path[srcXML]
        else:  # internal path, add-on
            path = self.path[srcXML] + '/' + key
        
        if PMS_address[0].isalpha():  # owned, shared
            type = self.PMS_address
            PMS = PlexAPI.getXMLFromMultiplePMS(self.ATV_udid, path, type, self.options)
        else:  # IP
            auth_token = PlexAPI.getPMSProperty(self.ATV_udid, self.PMS_uuid, 'accesstoken')
            PMS = PlexAPI.getXMLFromPMS(self.PMS_baseURL, path, self.options, auth_token)
        
        self.PMSroot[tag] = PMS.getroot()  # store additional PMS XML
        self.path[tag] = path  # store base path
        
        return False  # tree unchanged (well, source tree yes. but that doesn't count...)
    
    def TREE_VAR(self, elem, child, src, srcXML, param):
        var, leftover = self.getParam(src, param)
        key, leftover, dfltd = self.getKey(src, srcXML, leftover)
        conv, leftover = self.getConversion(src, leftover)
        if not dfltd:
            key = self.applyConversion(key, conv)
        
        self.variables[var] = key
        return False  # tree unchanged
    
    
    # XML ATTRIB modifier commands
    # add new commands to this list!
    def ATTRIB_VAL(self, src, srcXML, param):
        key, leftover, dfltd = self.getKey(src, srcXML, param)
        conv, leftover = self.getConversion(src, leftover)
        if not dfltd:
            key = self.applyConversion(key, conv)
        return key
    
    def ATTRIB_EVAL(self, src, srcXML, param):
        return str(eval(param))

    def ATTRIB_VAL_QUOTED(self, src, srcXML, param):
        key, leftover, dfltd = self.getKey(src, srcXML, param)
        conv, leftover = self.getConversion(src, leftover)
        if not dfltd:
            key = self.applyConversion(key, conv)
        return quote_plus(unicode(key).encode("utf-8"))

    def ATTRIB_SETTING(self, src, srcXML, param):
        opt, leftover = self.getParam(src, param)
        return g_ATVSettings.getSetting(self.ATV_udid, opt)
    
    def ATTRIB_ADDPATH(self, src, srcXML, param):
        addpath, leftover, dfltd = self.getKey(src, srcXML, param)
        if addpath.startswith('/'):
            res = addpath
        elif addpath == '':
            res = self.path[srcXML]
        else:
            res = self.path[srcXML]+'/'+addpath
        return res
    
    def ATTRIB_IMAGEURL(self, src, srcXML, param):
        key, leftover, dfltd = self.getKey(src, srcXML, param)
        width, leftover = self.getParam(src, leftover)
        height, leftover = self.getParam(src, leftover)
        if height=='':
            height = width
        
        PMS_uuid = self.PMS_uuid
        PMS_baseURL = self.PMS_baseURL
        cmd_start = key.find('PMS(')
        cmd_end = key.find(')', cmd_start)
        if cmd_start>-1 and cmd_end>-1 and cmd_end>cmd_start:
            PMS_address = key[cmd_start+4:cmd_end]
            PMS_uuid = PlexAPI.getPMSFromAddress(self.ATV_udid, PMS_address)
            PMS_baseURL = PlexAPI.getPMSProperty(self.ATV_udid, PMS_uuid, 'baseURL')
            key = key[cmd_end+1:]
        
        AuthToken = PlexAPI.getPMSProperty(self.ATV_udid, PMS_uuid, 'accesstoken')
        
        # transcoder action
        transcoderAction = g_ATVSettings.getSetting(self.ATV_udid, 'phototranscoderaction')
        
        # aTV native filetypes
        parts = key.rsplit('.',1)
        photoATVNative = parts[-1].lower() in ['jpg','jpeg','tif','tiff','gif','png']
        dprint(__name__, 2, "photo: ATVNative - {0}", photoATVNative)
        
        if width=='' and \
           transcoderAction=='Auto' and \
           photoATVNative:
            # direct play
            res = PlexAPI.getDirectImagePath(key, AuthToken)
        else:
            if width=='':
                width = 1920  # max for HDTV. Relate to aTV version? Increase for KenBurns effect?
            if height=='':
                height = 1080  # as above
            # request transcoding
            res = PlexAPI.getTranscodeImagePath(key, AuthToken, self.path[srcXML], width, height)
        
        if res.startswith('/'):  # internal full path.
            res = PMS_baseURL + res
        elif res.startswith('http://') or key.startswith('https://'):  # external address
            pass
        else:  # internal path, add-on
            res = PMS_baseURL + self.path[srcXML] + '/' + res
        
        dprint(__name__, 1, 'ImageURL: {0}', res)
        return res
    
    def ATTRIB_MUSICURL(self, src, srcXML, param):
        Track, leftover = self.getElement(src, srcXML, param)
        
        AuthToken = PlexAPI.getPMSProperty(self.ATV_udid, self.PMS_uuid, 'accesstoken')
        
        if not Track:
            # not a complete audio/track structure - take key directly and build direct-play path
            key, leftover, dfltd = self.getKey(src, srcXML, param)
            res = PlexAPI.getDirectAudioPath(key, AuthToken)
            res = PlexAPI.getURL(self.PMS_baseURL, self.path[srcXML], res)
            dprint(__name__, 1, 'MusicURL - direct: {0}', res)
            return res
        
        # complete track structure - request transcoding if needed
        Media = Track.find('Media')
        
        # check "Media" element and get key
        if Media!=None:
            # transcoder action setting?
            # transcoder bitrate setting [kbps] -  eg. 128, 256, 384, 512?
            maxAudioBitrate = '384'
            
            audioATVNative = \
                Media.get('audioCodec','-') in ("mp3", "aac", "ac3", "drms", "alac", "aiff", "wav")
            # check Media.get('container') as well - mp3, m4a, ...?
            
            dprint(__name__, 2, "audio: ATVNative - {0}", audioATVNative)
            
            if audioATVNative and\
               int(Media.get('bitrate','0')) < int(maxAudioBitrate):
                # direct play
                res, leftover, dfltd = self.getKey(Media, srcXML, 'Part/key')
                res = PlexAPI.getDirectAudioPath(res, AuthToken)
            else:
                # request transcoding
                res, leftover, dfltd = self.getKey(Track, srcXML, 'key')
                res = PlexAPI.getTranscodeAudioPath(res, AuthToken, self.options, maxAudioBitrate)
        
        else:
            dprint(__name__, 0, "MEDIAPATH - element not found: {0}", param)
            res = 'FILE_NOT_FOUND'  # not found?
        
        res = PlexAPI.getURL(self.PMS_baseURL, self.path[srcXML], res)
        dprint(__name__, 1, 'MusicURL: {0}', res)
        return res
    
    def ATTRIB_URL(self, src, srcXML, param):
        key, leftover, dfltd = self.getKey(src, srcXML, param)
        
        # compare PMS_mark in PlexAPI/getXMLFromMultiplePMS()
        PMS_mark = '/PMS(' + PlexAPI.getPMSProperty(self.ATV_udid, self.PMS_uuid, 'ip') + ')'
        
        # overwrite with URL embedded PMS address
        cmd_start = key.find('PMS(')
        cmd_end = key.find(')', cmd_start)
        if cmd_start>-1 and cmd_end>-1 and cmd_end>cmd_start:
            PMS_mark = '/'+key[cmd_start:cmd_end+1]
            key = key[cmd_end+1:]
        
        res = g_param['baseURL']  # base address to PlexConnect
        
        if key.endswith('.js'):  # link to PlexConnect owned .js stuff
            res = res + key
        elif key.startswith('http://') or key.startswith('https://'):  # external server
            res = key
            """
            parts = urlparse.urlsplit(key)  # (scheme, networklocation, path, ...)
            key = urlparse.urlunsplit(('', '', parts[2], parts[3], parts[4]))  # keep path only
            PMS_uuid = PlexAPI.getPMSFromIP(g_param['PMS_list'], parts.hostname)
            PMSaddress = PlexAPI.getAddress(g_param['PMS_list'], PMS_uuid)  # get PMS address (might be local as well!?!)
            res = res + '/PMS(' + quote_plus(PMSaddress) + ')' + key
            """
        elif key.startswith('/'):  # internal full path.
            res = res + PMS_mark + key
        elif key == '':  # internal path
            res = res + PMS_mark + self.path[srcXML]
        else:  # internal path, add-on
            res = res + PMS_mark + self.path[srcXML] + '/' + key
        
        return res
    
    def ATTRIB_VIDEOURL(self, src, srcXML, param):
        Video, leftover = self.getElement(src, srcXML, param)
        partIndex, leftover, dfltd = self.getKey(src, srcXML, leftover)
        partIndex = int(partIndex) if partIndex else 0
        
        AuthToken = PlexAPI.getPMSProperty(self.ATV_udid, self.PMS_uuid, 'accesstoken')
        
        if not Video:
            dprint(__name__, 0, "VIDEOURL - VIDEO element not found: {0}", param)
            res = 'VIDEO_ELEMENT_NOT_FOUND'  # not found?
            return res
        
        # complete video structure - request transcoding if needed
        Media = Video.find('Media')
        
        # check "Media" element and get key
        if Media!=None:
            # transcoder action
            transcoderAction = g_ATVSettings.getSetting(self.ATV_udid, 'transcoderaction')
            
            # video format
            #    HTTP live stream
            # or native aTV media
            videoATVNative = \
                Media.get('protocol','-') in ("hls") \
                or \
                Media.get('container','-') in ("mov", "mp4") and \
                Media.get('videoCodec','-') in ("mpeg4", "h264", "drmi") and \
                Media.get('audioCodec','-') in ("aac", "ac3", "drms")
            
            for Stream in Media.find('Part').findall('Stream'):
                if Stream.get('streamType','') == '1' and\
                   Stream.get('codec','-') in ("mpeg4", "h264"):
                    if Stream.get('profile', '-') == 'high 10' or \
                        int(Stream.get('refFrames','0')) > 8:
                            videoATVNative = False
                    break
            
            dprint(__name__, 2, "video: ATVNative - {0}", videoATVNative)
            
            # quality limits: quality=(resolution, quality, bitrate)
            qLookup = { '480p 2.0Mbps' :('720x480', '60', '2000'), \
                        '720p 3.0Mbps' :('1280x720', '75', '3000'), \
                        '720p 4.0Mbps' :('1280x720', '100', '4000'), \
                        '1080p 8.0Mbps' :('1920x1080', '60', '8000'), \
                        '1080p 10.0Mbps' :('1920x1080', '75', '10000'), \
                        '1080p 12.0Mbps' :('1920x1080', '90', '12000'), \
                        '1080p 20.0Mbps' :('1920x1080', '100', '20000'), \
                        '1080p 40.0Mbps' :('1920x1080', '100', '40000') }
            if PlexAPI.getPMSProperty(self.ATV_udid, self.PMS_uuid, 'local')=='1':
                qLimits = qLookup[g_ATVSettings.getSetting(self.ATV_udid, 'transcodequality')]
            else:
                qLimits = qLookup[g_ATVSettings.getSetting(self.ATV_udid, 'remotebitrate')]
            
            # subtitle renderer, subtitle selection
            subtitleRenderer = g_ATVSettings.getSetting(self.ATV_udid, 'subtitlerenderer')
            
            subtitleId = ''
            subtitleKey = ''
            subtitleFormat = ''
            for Stream in Media.find('Part').findall('Stream'):  # Todo: check 'Part' existance, deal with multi part video
                if Stream.get('streamType','') == '3' and\
                   Stream.get('selected','0') == '1':
                    subtitleId = Stream.get('id','')
                    subtitleKey = Stream.get('key','')
                    subtitleFormat = Stream.get('format','')
                    break
            
            subtitleIOSNative = \
                subtitleKey=='' and subtitleFormat=="tx3g"  # embedded
            subtitlePlexConnect = \
                subtitleKey!='' and subtitleFormat=="srt"  # external
            
            # subtitle suitable for direct play?
            #    no subtitle
            # or 'Auto'    with subtitle by iOS or PlexConnect
            # or 'iOS,PMS' with subtitle by iOS
            subtitleDirectPlay = \
                subtitleId=='' \
                or \
                subtitleRenderer=='Auto' and \
                ( (videoATVNative and subtitleIOSNative) or subtitlePlexConnect ) \
                or \
                subtitleRenderer=='iOS, PMS' and \
                (videoATVNative and subtitleIOSNative)
            dprint(__name__, 2, "subtitle: IOSNative - {0}, PlexConnect - {1}, DirectPlay - {2}", subtitleIOSNative, subtitlePlexConnect, subtitleDirectPlay)
            
            # determine video URL
            if transcoderAction=='DirectPlay' \
               or \
               transcoderAction=='Auto' and \
               videoATVNative and \
               int(Media.get('bitrate','0')) < int(qLimits[2]) and \
               subtitleDirectPlay:
                # direct play for...
                #    force direct play
                # or videoATVNative (HTTP live stream m4v/h264/aac...)
                #    limited by quality setting
                #    with aTV supported subtitle (iOS embedded tx3g, PlexConnext external srt)
                res, leftover, dfltd = self.getKey(Media, srcXML, 'Part['+str(partIndex+1)+']/key')
                
                if Media.get('indirect', False):  # indirect... todo: select suitable resolution, today we just take first Media
                    PMS = PlexAPI.getXMLFromPMS(self.PMS_baseURL, res, self.options, AuthToken)  # todo... check key for trailing '/' or even 'http'
                    res, leftover, dfltd = self.getKey(PMS.getroot(), srcXML, 'Video/Media/Part['+str(partIndex+1)+']/key')
                
                res = PlexAPI.getDirectVideoPath(res, AuthToken)
            else:
                # request transcoding
                res = Video.get('key','')
                
                # misc settings: subtitlesize, audioboost
                subtitle = { 'selected': '1' if subtitleId else '0', \
                             'dontBurnIn': '1' if subtitleDirectPlay else '0', \
                             'size': g_ATVSettings.getSetting(self.ATV_udid, 'subtitlesize') }
                audio = { 'boost': g_ATVSettings.getSetting(self.ATV_udid, 'audioboost') }
                res = PlexAPI.getTranscodeVideoPath(res, AuthToken, self.options, transcoderAction, qLimits, subtitle, audio, partIndex)
        
        else:
            dprint(__name__, 0, "VIDEOURL - MEDIA element not found: {0}", param)
            res = 'MEDIA_ELEMENT_NOT_FOUND'  # not found?
        
        if res.startswith('/'):  # internal full path.
            res = self.PMS_baseURL + res
        elif res.startswith('http://') or res.startswith('https://'):  # external address
            pass
        else:  # internal path, add-on
            res = self.PMS_baseURL + self.path[srcXML] + res
        
        dprint(__name__, 1, 'VideoURL: {0}', res)
        return res
    
    def ATTRIB_episodestring(self, src, srcXML, param):
        parentIndex, leftover, dfltd = self.getKey(src, srcXML, param)  # getKey "defaults" if nothing found.
        index, leftover, dfltd = self.getKey(src, srcXML, leftover)
        title, leftover, dfltd = self.getKey(src, srcXML, leftover)
        out = self._("{0:0d}x{1:02d} {2}").format(int(parentIndex), int(index), title)
        return out

    def ATTRIB_durationToString(self, src, srcXML, param):
        type, leftover, dfltd = self.getKey(src, srcXML, param)
        duration, leftover, dfltd = self.getKey(src, srcXML, leftover)
        if type == 'Video':
            min = int(duration)/1000/60
            if g_ATVSettings.getSetting(self.ATV_udid, 'durationformat') == 'Minutes':
                return self._("{0:d} Minutes").format(min)
            else:
                if len(duration) > 0:
                    hour = min/60
                    min = min%60
                    if hour == 0: return self._("{0:d} Minutes").format(min)
                    else: return self._("{0:d}hr {1:d}min").format(hour, min)
        
        if type == 'Audio':
            secs = int(duration)/1000
            if len(duration) > 0:
                mins = secs/60
                secs = secs%60
                return self._("{0:d}:{1:0>2d}").format(mins, secs)
        
        return ""
        
    def ATTRIB_contentRating(self, src, srcXML, param):
        rating, leftover, dfltd = self.getKey(src, srcXML, param)
        if rating.find('/') != -1:
            parts = rating.split('/')
            return parts[1]
        else:
            return rating
        
    def ATTRIB_unwatchedCountGrid(self, src, srcXML, param):
        total, leftover, dfltd = self.getKey(src, srcXML, param)
        viewed, leftover, dfltd = self.getKey(src, srcXML, leftover)
        unwatched = int(total) - int(viewed)
        return str(unwatched)
    
    def ATTRIB_unwatchedCountList(self, src, srcXML, param):
        total, leftover, dfltd = self.getKey(src, srcXML, param)
        viewed, leftover, dfltd = self.getKey(src, srcXML, leftover)
        unwatched = int(total) - int(viewed)
        if unwatched > 0: return self._("{0} unwatched").format(unwatched)
        else: return ""
    
    def ATTRIB_TEXT(self, src, srcXML, param):
        return self._(param)
    
    def ATTRIB_PMSCOUNT(self, src, srcXML, param):
        return str(PlexAPI.getPMSCount(self.ATV_udid))
    
    def ATTRIB_PMSNAME(self, src, srcXML, param):
        PMS_name = PlexAPI.getPMSProperty(self.ATV_udid, self.PMS_uuid, 'name')
        if PMS_name=='':
            return "No Server in Proximity"
        else:
            return PMS_name
    
    def ATTRIB_BACKGROUNDURL(self, src, srcXML, param):
        key, leftover, dfltd = self.getKey(src, srcXML, param)
        
        if key.startswith('/'):  # internal full path.
            key = self.PMS_baseURL + key
        elif key.startswith('http://') or key.startswith('https://'):  # external address
            pass
        else:  # internal path, add-on
            key = self.PMS_baseURL + self.path[srcXML] + key
        
        auth_token = PlexAPI.getPMSProperty(self.ATV_udid, self.PMS_uuid, 'accesstoken')
        
        dprint(__name__, 0, "Background (Source): {0}", key)
        res = g_param['baseURL']  # base address to PlexConnect
        res = res + PILBackgrounds.generate(self.PMS_uuid, key, auth_token, self.options['aTVScreenResolution'])
        dprint(__name__, 0, "Background: {0}", res)
        return res



if __name__=="__main__":
    cfg = Settings.CSettings()
    param = {}
    param['CSettings'] = cfg
    
    param['HostToIntercept'] = 'trailers.apple.com'
    setParams(param)
    
    cfg = ATVSettings.CATVSettings()
    setATVSettings(cfg)
    
    print "load PMS XML"
    _XML = '<PMS number="1" string="Hello"> \
                <DATA number="42" string="World"></DATA> \
                <DATA string="Sun"></DATA> \
            </PMS>'
    PMSroot = etree.fromstring(_XML)
    PMSTree = etree.ElementTree(PMSroot)
    print prettyXML(PMSTree)
    
    print
    print "load aTV XML template"
    _XML = '<aTV> \
                <INFO num="{{VAL(number)}}" str="{{VAL(string)}}">Info</INFO> \
                <FILE str="{{VAL(string)}}" strconv="{{VAL(string::World=big|Moon=small|Sun=huge)}}" num="{{VAL(number:5)}}" numfunc="{{EVAL(int({{VAL(number:5)}}/10))}}"> \
                    File{{COPY(DATA)}} \
                </FILE> \
                <PATH path="{{ADDPATH(file:unknown)}}" /> \
                <accessories> \
                    <cut />{{CUT(number::0=cut|1=)}} \
                    <dontcut />{{CUT(attribnotfound)}} \
                </accessories> \
                <ADDPATH>{{ADDPATH(string)}}</ADDPATH> \
                <COPY2>={{COPY(DATA)}}=</COPY2> \
            </aTV>'
    aTVroot = etree.fromstring(_XML)
    aTVTree = etree.ElementTree(aTVroot)
    print prettyXML(aTVTree)
    
    print
    print "unpack PlexConnect COPY/CUT commands"
    options = {}
    options['PlexConnectUDID'] = '007'
    PMS_address = 'PMS_IP'
    CommandCollection = CCommandCollection(options, PMSroot, PMS_address, '/library/sections')
    XML_ExpandTree(CommandCollection, aTVroot, PMSroot, 'main')
    XML_ExpandAllAttrib(CommandCollection, aTVroot, PMSroot, 'main')
    del CommandCollection
    
    print
    print "resulting aTV XML"
    print prettyXML(aTVTree)
    
    print
    #print "store aTV XML"
    #str = prettyXML(aTVTree)
    #f=open(sys.path[0]+'/XML/aTV_fromTmpl.xml', 'w')
    #f.write(str)
    #f.close()
    
    del cfg
