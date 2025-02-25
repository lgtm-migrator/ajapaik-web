# encoding: utf-8
import csv
import datetime
import json
import logging
import operator
import os
import re
import shutil
import ssl
import stat
import sys
import unicodedata
import urllib
from copy import deepcopy
from html import unescape
from io import StringIO
from math import ceil
from random import choice
from time import strftime, strptime, time
from urllib.request import build_opener
from uuid import uuid4
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import numpy
import cv2
import django_comments
import requests
from PIL import Image, ImageFile, ImageOps
from PIL.ExifTags import TAGS, GPSTAGS
from allauth.account.forms import AddEmailForm, ChangePasswordForm, SetPasswordForm
from allauth.account.views import EmailView, PasswordChangeView, PasswordSetView
from allauth.socialaccount.forms import DisconnectForm
from allauth.socialaccount.models import SocialAccount, SocialToken, SocialApp
from allauth.socialaccount.views import ConnectionsView
from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.gis.db.models.functions import Distance, GeometryDistance
from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.files import File
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.temp import NamedTemporaryFile
from django.db.models import Sum, Q, Count, F, Min, Max
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, get_object_or_404, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from django.views.decorators.http import condition
from django.views.generic.base import View
from django_comments.models import CommentFlag
from django_comments.signals import comment_was_flagged
from django_comments.views.comments import post_comment
from haystack.inputs import AutoQuery
from haystack.query import SearchQuerySet
from rest_framework.renderers import JSONRenderer
from sorl.thumbnail import delete
from sorl.thumbnail import get_thumbnail

from ajapaik.ajapaik.curator_drivers.common import CuratorSearchForm
from ajapaik.ajapaik.curator_drivers.europeana import EuropeanaDriver
from ajapaik.ajapaik.curator_drivers.finna import FinnaDriver
from ajapaik.ajapaik.curator_drivers.flickr_commons import FlickrCommonsDriver
from ajapaik.ajapaik.curator_drivers.fotis import FotisDriver
from ajapaik.ajapaik.curator_drivers.valimimoodul import ValimimoodulDriver
from ajapaik.ajapaik.curator_drivers.wikimediacommons import CommonsDriver
from ajapaik.ajapaik.forms import AddAlbumForm, AreaSelectionForm, AlbumSelectionForm, AddAreaForm, \
    CuratorPhotoUploadForm, CsvImportForm, GameAlbumSelectionForm, CuratorAlbumEditForm, ChangeDisplayNameForm, \
    SubmitGeotagForm, GameNextPhotoForm, GamePhotoSelectionForm, MapDataRequestForm, GalleryFilteringForm, \
    PhotoSelectionForm, SelectionUploadForm, ConfirmGeotagForm, AlbumInfoModalForm, PhotoLikeForm, \
    AlbumSelectionFilteringForm, DatingSubmitForm, DatingConfirmForm, VideoStillCaptureForm, \
    UserPhotoUploadForm, UserPhotoUploadAddAlbumForm, UserSettingsForm, \
    EditCommentForm, CuratorWholeSetAlbumsSelectionForm, RephotoUploadSettingsForm, OauthDoneForm
from ajapaik.ajapaik.models import Photo, Profile, Source, Device, DifficultyFeedback, GeoTag, MyXtdComment, Points, \
    Album, AlbumPhoto, Area, Licence, Skip, Transcription, _calc_trustworthiness, _get_pseudo_slug_for_photo, \
    MuisCollection, PhotoLike, PhotoFlipSuggestion, PhotoViewpointElevationSuggestion, PhotoSceneSuggestion, Dating, \
    DatingConfirmation, Video, ImageSimilarity, ImageSimilaritySuggestion, ProfileMergeToken, Supporter
from ajapaik.ajapaik.serializers import CuratorAlbumSelectionAlbumSerializer, CuratorMyAlbumListAlbumSerializer, \
    CuratorAlbumInfoSerializer, FrontpageAlbumSerializer, DatingSerializer, \
    VideoSerializer, PhotoMapMarkerSerializer
from ajapaik.ajapaik.stats_sql import AlbumStats
from ajapaik.ajapaik_face_recognition.models import FaceRecognitionRectangle
from ajapaik.ajapaik_object_recognition.models import ObjectDetectionAnnotation
from ajapaik.utils import get_etag, calculate_thumbnail_size, convert_to_degrees, calculate_thumbnail_size_max_height, \
    distance_in_meters, angle_diff, last_modified, suggest_photo_edit
from .utils import get_comment_replies, get_pagination_parameters

log = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 933120000
ImageFile.LOAD_TRUNCATED_IMAGES = True


def image_thumb(request, photo_id=None, thumb_size=250, pseudo_slug=None):
    thumb_size = int(thumb_size)
    if 0 < thumb_size <= 400:
        thumb_size = 400
    else:
        thumb_size = 1024
    p = get_object_or_404(Photo, id=photo_id)
    thumb_str = f'{str(thumb_size)}x{str(thumb_size)}'
    if p.rephoto_of:
        original_thumb = get_thumbnail(p.rephoto_of.image, thumb_str, upscale=False)
        thumb_str = f'{str(original_thumb.size[0])}x{str(original_thumb.size[1])}'
        # TODO: see if restricting Pillow version fixes this
        im = get_thumbnail(p.image, thumb_str, upscale=True, downscale=True, crop='center')
    else:
        im = get_thumbnail(p.image, thumb_str, upscale=False)
    try:
        content = im.read()
    except IOError:
        delete(im)
        im = get_thumbnail(p.image, thumb_str, upscale=False)
        content = im.read()

    return get_image_thumb(request, f'{settings.MEDIA_ROOT}/{im.name}', content)


@condition(etag_func=get_etag, last_modified_func=last_modified)
@cache_control(must_revalidate=True)
def get_image_thumb(request, image, content):
    return HttpResponse(content, content_type='image/jpg')


@cache_control(max_age=604800)
def image_full(request, photo_id=None, pseudo_slug=None):
    p = get_object_or_404(Photo, id=photo_id)
    content = p.image.read()

    return HttpResponse(content, content_type='image/jpg')


def get_general_info_modal_content(request):
    profile = request.get_user().profile
    photo_qs = Photo.objects.filter(rephoto_of__isnull=True)
    rephoto_qs = Photo.objects.filter(rephoto_of__isnull=False)
    geotags_qs = GeoTag.objects.filter()
    cached_data = cache.get('general_info_modal_cache', None)
    person_annotation_qs = FaceRecognitionRectangle.objects.filter(deleted=None)
    person_annotation_with_person_album_qs = person_annotation_qs.exclude(subject_consensus=None)
    person_annotation_with_subject_data_qs = person_annotation_qs.exclude(Q(gender=None) & Q(age=None))

    if cached_data is None:
        cached_data = {
            'photos_count': photo_qs.count(),
            'contributing_users_count': geotags_qs.distinct('user').count(),
            'photos_geotagged_count': photo_qs.filter(lat__isnull=False, lon__isnull=False).count(),
            'rephotos_count': rephoto_qs.count(),
            'rephotographing_users_count': rephoto_qs.order_by('user').distinct('user').count(),
            'photos_with_rephotos_count': rephoto_qs.order_by('rephoto_of_id').distinct('rephoto_of_id').count(),
            'person_annotation_count': person_annotation_qs.count(),
            'person_annotation_count_with_person_album': person_annotation_with_person_album_qs.count(),
            'person_annotation_count_with_subject_data': person_annotation_with_subject_data_qs.count()
        }
        cache.set('general_info_modal_cache', cached_data, settings.GENERAL_INFO_MODAL_CACHE_TTL)
    context = {
        'user': request.get_user(),
        'total_photo_count': cached_data['photos_count'],
        'contributing_users': cached_data['contributing_users_count'],
        'total_photos_tagged': cached_data['photos_geotagged_count'],
        'rephoto_count': cached_data['rephotos_count'],
        'rephotographing_users': cached_data['rephotographing_users_count'],
        'rephotographed_photo_count': cached_data['photos_with_rephotos_count'],
        'person_annotation_count': cached_data['person_annotation_count'],
        'person_annotation_count_with_person_album': cached_data['person_annotation_count_with_person_album'],
        'person_annotation_count_with_subject_data': cached_data['person_annotation_count_with_subject_data'],
    }

    return render(request, 'info/_general_info_modal_content.html', context)


def get_album_info_modal_content_old(request):
    profile = request.get_user().profile
    form = AlbumInfoModalForm(request.GET)
    if form.is_valid():
        album = form.cleaned_data['album']
        context = {'album': album, 'link_to_map': form.cleaned_data['linkToMap'],
                   'link_to_game': form.cleaned_data['linkToGame'],
                   'link_to_gallery': form.cleaned_data['linkToGallery'],
                   'fb_share_game': form.cleaned_data['fbShareGame'], 'fb_share_map': form.cleaned_data['fbShareMap'],
                   'fb_share_gallery': form.cleaned_data['fbShareGallery'],
                   'total_photo_count': album.photo_count_with_subalbums,
                   'geotagged_photo_count': album.geotagged_photo_count_with_subalbums}

        album_photo_ids = album.get_all_photos_queryset_with_subalbums().values_list('id', flat=True)
        geotags_for_album_photos = GeoTag.objects.filter(photo_id__in=album_photo_ids)
        context['user_geotagged_photo_count'] = geotags_for_album_photos.filter(user=profile).distinct(
            'photo_id').count()
        context['geotagging_user_count'] = geotags_for_album_photos.distinct('user').count()

        context['rephoto_count'] = album.rephoto_count_with_subalbums
        rephotos_qs = album.get_rephotos_queryset_with_subalbums()
        context['rephoto_user_count'] = rephotos_qs.order_by('user_id').distinct('user_id').count()
        context['rephotographed_photo_count'] = rephotos_qs.order_by('rephoto_of_id').distinct('rephoto_of_id').count()

        album_user_rephotos = rephotos_qs.filter(user=profile)
        context['user_rephoto_count'] = album_user_rephotos.count()
        context['user_rephotographed_photo_count'] = album_user_rephotos.order_by('rephoto_of_id').distinct(
            'rephoto_of_id').count()
        if context['rephoto_user_count'] == 1 and context['user_rephoto_count'] == context['rephoto_count']:
            context['user_made_all_rephotos'] = True
        else:
            context['user_made_all_rephotos'] = False

        context['similar_photo_count'] = album.similar_photo_count_with_subalbums
        context['confirmed_similar_photo_count'] = album.confirmed_similar_photo_count_with_subalbums

        # Get all users that have either curated into selected photo set or re-curated into selected album
        users_curated_to_album = AlbumPhoto.objects.filter(
            photo_id__in=album_photo_ids, profile__isnull=False, album=album,
            type__in=[AlbumPhoto.UPLOADED, AlbumPhoto.CURATED, AlbumPhoto.RECURATED]
        ).values('profile').annotate(count=Count('profile'))

        user_score_dict = {}
        for u in users_curated_to_album:
            user_score_dict[u['profile']] = u['count']

        album_curators = Profile.objects.filter(user_id__in=user_score_dict.keys(), first_name__isnull=False,
                                                last_name__isnull=False)
        user_score_dict = [x[0] for x in sorted(user_score_dict.items(), key=operator.itemgetter(1), reverse=True)]
        album_curators = list(album_curators)
        album_curators.sort(key=lambda z: user_score_dict.index(z.id))
        context['album_curators'] = album_curators

        if album.lat and album.lon:
            context['nearby_albums'] = Album.objects \
                                           .filter(
                geography__distance_lte=(Point(album.lon, album.lat), D(m=50000)),
                is_public=True,
                atype=Album.CURATED,
                id__ne=album.id
            ) \
                                           .order_by('?')[:3]
        album_id_str = str(album.id)
        context['share_game_link'] = f'{request.build_absolute_uri(reverse("game"))}?album={album_id_str}'
        context['share_map_link'] = f'{request.build_absolute_uri(reverse("map"))}?album={album_id_str}'
        context['share_gallery_link'] = f'{request.build_absolute_uri(reverse("frontpage"))}?album={album_id_str}'

        return render(request, 'info/_info_modal_content.html', context)

    return HttpResponse('Error')

# 2022-11-02 faster rewrite of get_album_info_modal_content() query
# number of rephoto_user_count and geotagging_user_count is 1 smaller
# than old because different way to handle NULL:s

def get_album_info_modal_content(request):
    starttime=time()

    profile = request.get_user().profile
    form = AlbumInfoModalForm(request.GET)
    if form.is_valid():
        album = form.cleaned_data['album']
        context = {'album': album, 'link_to_map': form.cleaned_data['linkToMap'],
                   'link_to_game': form.cleaned_data['linkToGame'],
                   'link_to_gallery': form.cleaned_data['linkToGallery'],
                   'fb_share_game': form.cleaned_data['fbShareGame'], 'fb_share_map': form.cleaned_data['fbShareMap'],
                   'fb_share_gallery': form.cleaned_data['fbShareGallery'],
                   'total_photo_count': album.photo_count_with_subalbums or 0,
                   'geotagged_photo_count': album.geotagged_photo_count_with_subalbums}

        subalbums = [album.id]
        for sa in album.subalbums.filter(atype__in=[Album.CURATED, Album.PERSON]):
            subalbums.append(sa.id)

        rephotostats=AlbumStats.get_rephoto_stats_sql(subalbums, profile.pk)

        context['user_geotagged_photo_count']     = AlbumStats.get_user_geotagged_photo_count_sql(subalbums, profile.pk)
        context['geotagging_user_count']          = AlbumStats.get_geotagging_user_count_sql(subalbums)
        context['rephoto_count']                  = rephotostats["rephoto_count"]
        context['rephoto_user_count']             = rephotostats["rephoto_user_count"] 
        context['rephotographed_photo_count']     = rephotostats["rephotographed_photo_count"] 
        context['user_rephoto_count']             = rephotostats["user_rephoto_count"]
        context['user_rephotographed_photo_count']= rephotostats["user_rephotographed_photo_count"]
        context['user_made_all_rephotos']         = rephotostats['user_made_all_rephotos']
        context['similar_photo_count']            = album.similar_photo_count_with_subalbums 
        context['confirmed_similar_photo_count']  = album.confirmed_similar_photo_count_with_subalbums
        context['album_curators']                 = AlbumStats.get_album_curators_sql([album.id])

        if album.lat and album.lon:
            ref_location = Point(x=album.lon, y=album.lat, srid=4326)
            context['nearby_albums'] = Album.objects \
                                           .filter(
                    geography__dwithin=(ref_location, D(m=5000)),
                    is_public=True,
                    atype=Album.CURATED,
                    id__ne=album.id
                ).order_by('?')[:3]

        album_id_str = str(album.id)
        context['share_game_link'] = f'{request.build_absolute_uri(reverse("game"))}?album={album_id_str}'
        context['share_map_link'] = f'{request.build_absolute_uri(reverse("map"))}?album={album_id_str}'
        context['share_gallery_link'] = f'{request.build_absolute_uri(reverse("frontpage"))}?album={album_id_str}'
        context['execution_time']                 = starttime-time()

        return render(request, 'info/_info_modal_content.html', context)

    return HttpResponse('Error')



def _get_exif_data(img):
    try:
        exif = img._getexif()
    except (AttributeError, IOError, KeyError, IndexError):
        exif = None
    if exif is None:
        return None
    exif_data = {}
    for (tag, value) in exif.items():
        decoded = TAGS.get(tag, tag)
        if decoded == 'GPSInfo':
            for t in value:
                sub_decoded = GPSTAGS.get(t, t)
                exif_data[f'{str(decoded)}.{str(sub_decoded)}'] = value[t]
        elif len(str(value)) < 50:
            exif_data[decoded] = value
        else:
            exif_data[decoded] = None

    return exif_data


def _extract_and_save_data_from_exif(photo_with_exif):
    img = Image.open(f'{settings.MEDIA_ROOT}/{str(photo_with_exif.image)}')
    exif_data = _get_exif_data(img)
    if exif_data:
        if 'GPSInfo.GPSLatitudeRef' in exif_data and 'GPSInfo.GPSLatitude' in exif_data and 'GPSInfo.GPSLongitudeRef' \
                in exif_data and 'GPSInfo.GPSLongitude' in exif_data:
            gps_latitude_ref = exif_data.get('GPSInfo.GPSLatitudeRef')
            gps_latitude = exif_data.get('GPSInfo.GPSLatitude')
            gps_longitude_ref = exif_data.get('GPSInfo.GPSLongitudeRef')
            gps_longitude = exif_data.get('GPSInfo.GPSLongitude')
            try:
                lat = convert_to_degrees(gps_latitude)
                if gps_latitude_ref != 'N':
                    lat = 0 - lat
                lon = convert_to_degrees(gps_longitude)
                if gps_longitude_ref != 'E':
                    lon = 0 - lon
                photo_with_exif.lat = lat
                photo_with_exif.lon = lon
                photo_with_exif.save()
            except:
                print("convert_to_degrees() failed")

        if 'Make' in exif_data or 'Model' in exif_data or 'LensMake' in exif_data or 'LensModel' in exif_data \
                or 'Software' in exif_data:
            camera_make = exif_data.get('Make')
            camera_model = exif_data.get('Model')
            lens_make = exif_data.get('LensMake')
            lens_model = exif_data.get('LensModel')
            software = exif_data.get('Software')
            try:
                device = Device.objects.get(camera_make=camera_make, camera_model=camera_model, lens_make=lens_make,
                                            lens_model=lens_model, software=software)
            except ObjectDoesNotExist:
                try:
                    device = Device(camera_make=camera_make, camera_model=camera_model, lens_make=lens_make,
                                    lens_model=lens_model, software=software)
                    device.save()
                except:  # noqa
                    device = None
            photo_with_exif.device = device
            photo_with_exif.save()
        if 'DateTimeOriginal' in exif_data and not photo_with_exif.date:
            date_taken = exif_data.get('DateTimeOriginal')
            try:
                parsed_time = strptime(date_taken, '%Y:%m:%d %H:%M:%S')
            except ValueError:
                parsed_time = None
            if parsed_time:
                parsed_time = strftime('%H:%M:%S', parsed_time)
            # ignore default camera dates
            if parsed_time and parsed_time != '12:00:00' and parsed_time != '00:00:00':
                try:
                    parsed_date = strptime(date_taken, '%Y:%m:%d %H:%M:%S')
                except ValueError:
                    parsed_date = None
                if parsed_date:
                    photo_with_exif.date = strftime('%Y-%m-%d', parsed_date)
                    photo_with_exif.save()
        return True
    else:
        return False


def _get_album_choices(qs=None, start=None, end=None):
    # TODO: Sort out
    if qs != None and qs.exists():
        albums = qs.prefetch_related('cover_photo').order_by('-created')[start:end]
    else:
        albums = Album.objects.filter(is_public=True).prefetch_related('cover_photo').order_by('-created')[start:end]
    for a in albums:
        if a.cover_photo:
            a.cover_photo_width, a.cover_photo_height = calculate_thumbnail_size(a.cover_photo.width,
                                                                                 a.cover_photo.height, 400)
        else:
            a.cover_photo_width, a.cover_photo_height = 400, 300

    return albums


def _calculate_recent_activity_scores():
    c = min(5000, Points.objects.all().count())
    recent_actions = []
    if c > 0:
        five_thousand_actions_ago = Points.objects.order_by('-created')[c - 1].created
        recent_actions = Points.objects.filter(created__gt=five_thousand_actions_ago).values('user_id') \
            .annotate(total_points=Sum('points'))
    recent_action_dict = {}
    for each in recent_actions:
        recent_action_dict[each['user_id']] = each['total_points']
    recent_actors = Profile.objects.filter(pk__in=recent_action_dict.keys())
    for each in recent_actors:
        each.score_recent_activity = recent_action_dict[each.pk]
        each.save()
    # Profile.objects.bulk_update(recent_actors, update_fields=['score_recent_activity'])
    # Check for people who somehow no longer have actions among the last 5000
    orphan_profiles = Profile.objects.filter(score_recent_activity__gt=0).exclude(pk__in=[x.pk for x in recent_actors])
    orphan_profiles.update(score_recent_activity=0)


def _get_leaderboard(profile):
    # General small leaderboard doesn't have anonymous users, displays recent activity score
    # TODO: Should also show first place, where did that code go?
    profile_rank = Profile.objects.filter(score_recent_activity__gt=profile.score_recent_activity,
                                          first_name__isnull=False, last_name__isnull=False).count() + 1
    leaderboard_queryset = Profile.objects.filter(
        Q(first_name__isnull=False, last_name__isnull=False, score_recent_activity__gt=0) |
        Q(pk=profile.id)).order_by('-score_recent_activity')
    start = profile_rank - 2
    if start < 0:
        start = 0
    nearby_users = leaderboard_queryset[start:profile_rank + 1]
    n = start + 1
    for each in nearby_users:
        if each == profile:
            each.is_current_user = True
        each.position = n
        n += 1

    return nearby_users


# TODO: Leaderboards should be generated by cron jobs
def _get_album_leaderboard50(profile_id, album_id=None):
    album = Album.objects.get(pk=album_id)
    album_photos_qs = album.get_historic_photos_queryset_with_subalbums()
    album_photo_ids = frozenset(album_photos_qs.values_list('id', flat=True))
    album_photos_with_rephotos = album_photos_qs.filter(rephotos__isnull=False).prefetch_related('rephotos')
    album_rephoto_ids = []
    for each in album_photos_with_rephotos:
        for rp in each.rephotos.all():
            album_rephoto_ids.append(rp.id)
    photo_points = Points.objects.prefetch_related('user') \
        .filter(photo_id__in=album_photo_ids, points__gt=0)
    photo_points = photo_points | Points.objects.prefetch_related('user') \
        .filter(photo_id__in=album_rephoto_ids, points__gt=0).exclude(action=Points.PHOTO_RECURATION)
    photo_points = photo_points | Points.objects.filter(photo_id__in=album_photo_ids, album=album,
                                                        action=Points.PHOTO_RECURATION).prefetch_related('user')
    # TODO: This should not be done in Python memory, but with a query
    user_score_map = {}
    for each in photo_points:
        if each.user_id in user_score_map:
            user_score_map[each.user_id] += each.points
        else:
            user_score_map[each.user_id] = each.points
    if profile_id not in user_score_map:
        user_score_map[profile_id] = 0
    sorted_scores = sorted(user_score_map.items(), key=operator.itemgetter(1), reverse=True)[:50]
    pk_list = [x[0] for x in sorted_scores]
    try:
        current_user_rank = pk_list.index(profile_id)
    except ValueError:
        current_user_rank = len(sorted_scores)
    current_user_rank += 1
    # Works on Postgres, we don't really need to worry about this I guess...maybe only if it gets slow
    clauses = ' '.join(['WHEN user_id=%s THEN %s' % (pk, i) for i, pk in enumerate(pk_list)])
    ordering = 'CASE %s END' % clauses
    top_users = Profile.objects.filter(Q(user_id__in=pk_list) | Q(user_id=profile_id)) \
        .extra(select={'ordering': ordering}, order_by=('ordering',)).prefetch_related('user')
    n = 1
    for each in top_users:
        if each.user_id == profile_id:
            each.is_current_user = True
        each.custom_score = user_score_map[each.user_id]
        each.position = n
        n += 1

    return top_users, album.name


def _get_all_time_leaderboard50(profile_id):
    lb = Profile.objects.filter(
        Q(first_name__isnull=False, last_name__isnull=False) |
        Q(pk=profile_id)).order_by('-score').prefetch_related('user')[:50]
    n = 1
    for each in lb:
        if each.user_id == profile_id:
            each.is_current_user = True
        each.position = n
        n += 1

    return lb


@csrf_exempt
def rephoto_upload(request, photo_id):
    photo = get_object_or_404(Photo, pk=photo_id)
    new_id = 0
    if request.method == 'POST':
        profile = request.get_user().profile
        user = request.get_user()
        social_account = SocialAccount.objects.filter(user=request.user).first()
        if not social_account and not user.email:
            return HttpResponse(json.dumps({'error': _('Non-authenticated user')}), content_type='application/json')
        if 'user_file[]' in request.FILES.keys():
            for f in request.FILES.getlist('user_file[]'):
                file_obj = ContentFile(f.read())
                data = request.POST
                date_taken = data.get('dateTaken', None)
                re_photo = Photo(
                    rephoto_of=photo,
                    area=photo.area,
                    licence=Licence.objects.get(id=17),  # CC BY 4.0
                    description=data.get('description', photo.get_display_text),
                    lat=data.get('lat', None),
                    lon=data.get('lon', None),
                    date_text=data.get('date_text', None),
                    user=profile,
                    cam_scale_factor=data.get('scale_factor', None),
                    cam_yaw=data.get('yaw'),
                    cam_pitch=data.get('pitch'),
                    cam_roll=data.get('roll'),
                )
                if date_taken is not None:
                    try:
                        parsed_date_taken = strptime(date_taken, '%d.%m.%Y %H:%M')
                        re_photo.date = strftime('%Y-%m-%d %H:%M', parsed_date_taken)
                    except:  # noqa
                        pass
                else:
                    re_photo.date = timezone.now()
                if re_photo.cam_scale_factor:
                    re_photo.cam_scale_factor = round(float(re_photo.cam_scale_factor), 6)
                re_photo.save()
                photo.save()
                for each in photo.albums.all():
                    each.rephoto_count_with_subalbums = each.get_rephotos_queryset_with_subalbums().count()
                    each.light_save()
                re_photo.image.save('rephoto.jpg', file_obj)
                # Image saved to disk, can analyse now
                re_photo.set_aspect_ratio()
                re_photo.find_similar()
                new_id = re_photo.pk
                img = Image.open(f'{settings.MEDIA_ROOT}/{str(re_photo.image)}')
                _extract_and_save_data_from_exif(re_photo)

                if re_photo.cam_scale_factor:
                    new_size = tuple([int(x * re_photo.cam_scale_factor) for x in img.size])
                    output_file = StringIO()
                    if re_photo.cam_scale_factor < 1:
                        x0 = (img.size[0] - new_size[0]) / 2
                        y0 = (img.size[1] - new_size[1]) / 2
                        x1 = img.size[0] - x0
                        y1 = img.size[1] - y0
                        new_img = img.transform(new_size, Image.EXTENT, (x0, y0, x1, y1))
                        new_img.save(output_file, 'JPEG', quality=95)
                        re_photo.image_unscaled = deepcopy(re_photo.image)
                        re_photo.image.save(str(re_photo.image), ContentFile(output_file.getvalue()))
                    elif re_photo.cam_scale_factor > 1:
                        x0 = (new_size[0] - img.size[0]) / 2
                        y0 = (new_size[1] - img.size[1]) / 2
                        new_img = Image.new('RGB', new_size)
                        new_img.paste(img, (x0, y0))
                        new_img.save(output_file, 'JPEG', quality=95)
                        re_photo.image_unscaled = deepcopy(re_photo.image)
                        re_photo.image.save(str(re_photo.image), ContentFile(output_file.getvalue()))

        profile.update_rephoto_score()
        profile.set_calculated_fields()
        profile.save()

    return HttpResponse(json.dumps({'new_id': new_id}), content_type='application/json')


def logout(request):
    from django.contrib.auth import logout

    logout(request)

    if 'HTTP_REFERER' in request.META:
        return redirect(request.META['HTTP_REFERER'])

    return redirect('/')


@ensure_csrf_cookie
def game(request):
    profile = request.get_user().profile
    user_has_likes = profile.likes.exists()
    user_has_rephotos = profile.photos.filter(rephoto_of__isnull=False).exists()
    area_selection_form = AreaSelectionForm(request.GET)
    album_selection_form = AlbumSelectionForm(
        request.GET,
        initial={'album': Album.objects.filter(is_public=True).order_by('-created').first()}
    )
    game_album_selection_form = GameAlbumSelectionForm(request.GET)
    game_photo_selection_form = GamePhotoSelectionForm(request.GET)
    album = None
    area = None
    context = {
        'albums': _get_album_choices(None, 0, 1)  # Where this is used? Ie. is albums variable used at all
    }

    if game_photo_selection_form.is_valid():
        p = game_photo_selection_form.cleaned_data['photo']
        context['photo'] = p
        album_ids = AlbumPhoto.objects.filter(photo_id=p.id).distinct('album_id').values_list('album_id', flat=True)
        album = Album.objects.filter(id__in=album_ids, atype=Album.CURATED).order_by('-created').first()
    elif game_album_selection_form.is_valid():
        album = game_album_selection_form.cleaned_data['album']
    else:
        if area_selection_form.is_valid():
            area = area_selection_form.cleaned_data['area']
        else:
            old_city_id = request.GET.get('city__pk') or None
            if old_city_id is not None:
                area = Area.objects.get(pk=old_city_id)
        context['area'] = area

    facebook_share_photos = None
    if album:
        context['album'] = (album.id, album.name, album.lat, album.lon, ','.join(album.name.split(' ')))
        qs = album.photos.filter(rephoto_of__isnull=True)
        for sa in album.subalbums.exclude(atype=Album.AUTO):
            qs = qs | sa.photos.filter(rephoto_of__isnull=True)
        context['album_photo_count'] = qs.distinct('id').count()
        facebook_share_photos = album.photos.all()
    elif area:
        facebook_share_photos = Photo.objects.filter(area=area, rephoto_of__isnull=True).order_by('?')

    context['facebook_share_photos'] = []
    if facebook_share_photos:
        for each in facebook_share_photos[:5]:
            context['facebook_share_photos'].append([each.pk, each.get_pseudo_slug(), each.width, each.height])

    context['hostname'] = request.build_absolute_uri('/')
    if album:
        context['title'] = album.name
    elif area:
        context['title'] = area.name
    else:
        context['title'] = _('Geotagging game')
    context['is_game'] = True
    context['area_selection_form'] = area_selection_form
    context['album_selection_form'] = album_selection_form
    context['last_geotagged_photo_id'] = Photo.objects.order_by(F('latest_geotag').desc(nulls_last=True)).first().id
    context['ajapaik_facebook_link'] = settings.AJAPAIK_FACEBOOK_LINK
    context['user_has_likes'] = user_has_likes
    context['user_has_rephotos'] = user_has_rephotos

    return render(request, 'common/game.html', context)


def fetch_stream(request):
    profile = request.get_user().profile
    form = GameNextPhotoForm(request.GET)
    data = {'photo': None, 'userSeenAll': False, 'nothingMoreToShow': False}
    if form.is_valid():
        qs = Photo.objects.filter(rephoto_of__isnull=True)
        form_area = form.cleaned_data['area']
        form_album = form.cleaned_data['album']
        form_photo = form.cleaned_data['photo']
        # TODO: Correct implementation
        if form_photo:
            form_photo.user_already_confirmed = False
            last_confirm_geotag_by_this_user_for_photo = form_photo.geotags.filter(user_id=profile.id,
                                                                                   type=GeoTag.CONFIRMATION).order_by(
                '-created').first()
            if last_confirm_geotag_by_this_user_for_photo and (
                    form_photo.lat == last_confirm_geotag_by_this_user_for_photo.lat
                    and form_photo.lon == last_confirm_geotag_by_this_user_for_photo.lon):
                form_photo.user_already_confirmed = True
            form_photo.user_already_geotagged = form_photo.geotags.filter(user_id=profile.id).exists()
            form_photo.user_likes = PhotoLike.objects.filter(profile=profile, photo=form_photo, level=1).exists()
            form_photo.user_loves = PhotoLike.objects.filter(profile=profile, photo=form_photo, level=2).exists()
            form_photo.user_like_count = PhotoLike.objects.filter(photo=form_photo).distinct('profile').count()
            data = {'photo': Photo.get_game_json_format_photo(form_photo), 'userSeenAll': False,
                    'nothingMoreToShow': False}
        else:
            if form_album:
                # TODO: Could be done later where we're frying our brains with nextPhoto logic anyway
                photos_ids_in_album = list(form_album.photos.values_list('id', flat=True))
                subalbums = form_album.subalbums.exclude(atype=Album.AUTO)
                for sa in subalbums:
                    photos_ids_in_subalbum = list(sa.photos.values_list('id', flat=True))
                    photos_ids_in_album += photos_ids_in_subalbum
                qs = qs.filter(pk__in=photos_ids_in_album)
            elif form_area:
                qs = qs.filter(area=form_area)
            # FIXME: Ugly
            try:
                response = Photo.get_next_photo_to_geotag(qs, request)
                data = {'photo': response[0], 'userSeenAll': response[1], 'nothingMoreToShow': response[2]}
            except IndexError:
                pass

    return HttpResponse(json.dumps(data), content_type='application/json')


# Params for old URL support
def frontpage(request, album_id=None, page=None):
    profile = request.get_user().profile
    data = _get_filtered_data_for_frontpage(request, album_id, page)

    user_has_likes = profile.likes.exists()
    user_has_rephotos = profile.photos.filter(rephoto_of__isnull=False).exists()

    if data['rephotos_by_name']:
        title = _('%(name)s - rephotos') % {'name': data['rephotos_by_name']}
    elif data['album']:
        title = data['album'][1]
    else:
        title = ''

    # Using "nulls last" here as it uses same index
    # which is already used in _get_filtered_data_for_frontpage()
    last_geotagged_photo = Photo.objects.order_by(F('latest_geotag').desc(nulls_last=True)).first()
    filters = ['film', 'collections', 'people', 'backsides', 'exteriors', 'interiors',
               'portrait', 'square', 'landscape', 'panoramic', 'ground_viewpoint_elevation',
               'raised_viewpoint_elevation', 'aerial_viewpoint_elevation', 'no_geotags', 'high_quality'
               ]
    highlight_filter_icon = (data['order2'] != 'added' or data['order3'] == 'reverse') or \
                            len([filter for filter in filters if filter in request.GET]) > 0
    context = {
        'is_frontpage': True,
        'title': title,
        'hostname': request.build_absolute_uri('/'),
        'ajapaik_facebook_link': settings.AJAPAIK_FACEBOOK_LINK,
        'facebook_share_photos': data['fb_share_photos'],
        'album': data['album'],
        'photo': data['photo'],
        'page': data['page'],
        'order1': data['order1'],
        'order2': data['order2'],
        'order3': data['order3'],
        'user_has_likes': user_has_likes,
        'user_has_rephotos': user_has_rephotos,
        'my_likes_only': data['my_likes_only'],
        'rephotos_by': data['rephotos_by'],
        'rephotos_by_name': data['rephotos_by_name'],
        'photos_with_comments': data['photos_with_comments'],
        'photos_with_rephotos': data['photos_with_rephotos'],
        'photos_with_similar_photos': data['photos_with_similar_photos'],
        'show_photos': data['show_photos'],
        'is_photoset': data['is_photoset'],
        'last_geotagged_photo_id': last_geotagged_photo.id if last_geotagged_photo else None,
        'highlight_filter_icon': highlight_filter_icon
    }

    return render(request, 'common/frontpage.html', context)


def frontpage_async_data(request):
    data = _get_filtered_data_for_frontpage(request)
    data['fb_share_photos'] = None

    return HttpResponse(json.dumps(data), content_type='application/json')


def frontpage_async_albums(request):
    form = AlbumSelectionFilteringForm(request.GET)
    context = {}
    if form.is_valid():
        page = form.cleaned_data['page']
        if page is None:
            page = 1
        page_size = settings.FRONTPAGE_DEFAULT_ALBUM_PAGE_SIZE
        start = (page - 1) * page_size
        albums = Album.objects
        if form.cleaned_data['people']:
            albums = albums.filter(cover_photo__isnull=False, atype=Album.PERSON)
        if form.cleaned_data['collections']:
            albums = albums.filter(atype=Album.COLLECTION, cover_photo__isnull=False, is_public=True)
        if form.cleaned_data['film']:
            albums = albums.filter(is_film_still_album=True, cover_photo__isnull=False, is_public=True)
        if albums == Album.objects:
            albums = albums.exclude(atype__in=[Album.AUTO, Album.FAVORITES]).filter(
                cover_photo__isnull=False,
                is_public=True
            )
        q = form.cleaned_data['q']
        if q:
            sqs = SearchQuerySet().models(Album).filter(content=AutoQuery(q))
            albums = albums.filter(pk__in=[r.pk for r in sqs])
        total = albums.count()
        if start < 0:
            start = 0
        if start > total:
            start = total
        if int(start + page_size) > total:
            end = total
        else:
            end = start + page_size
        end = int(end)
        max_page = int(ceil(float(total) / float(page_size)))

        albums = _get_album_choices(albums, start, end)
        serializer = FrontpageAlbumSerializer(albums, many=True)
        context['start'] = start
        context['end'] = end
        context['total'] = total
        context['max_page'] = max_page
        context['page'] = page
        context['albums'] = serializer.data
    return HttpResponse(json.dumps(context), content_type='application/json')


def _get_filtered_data_for_frontpage(request, album_id=None, page_override=None):
    starttime = time()
    profile = request.get_user().profile
    photos = Photo.objects.filter(rephoto_of__isnull=True)
    filter_form = GalleryFilteringForm(request.GET)
    page_size = settings.FRONTPAGE_DEFAULT_PAGE_SIZE
    context = {}
    if filter_form.is_valid():
        if album_id:
            album = Album.objects.get(pk=album_id)
        else:
            album = filter_form.cleaned_data['album']
        requested_photo = filter_form.cleaned_data['photo']
        requested_photos = filter_form.cleaned_data['photos']
        order1 = filter_form.cleaned_data['order1']
        order2 = filter_form.cleaned_data['order2']
        order3 = filter_form.cleaned_data['order3']
        default_ordering = False
        if not order1 and not order2:
            order1 = 'time'
            order2 = 'added'
            default_ordering = True
        context['order1'] = order1
        context['order2'] = order2
        context['order3'] = order3
        my_likes_only = filter_form.cleaned_data['myLikes']
        rephotos_by_name = None
        rephotos_by_id = None
        if filter_form.cleaned_data['rephotosBy']:
            rephotos_by_name = filter_form.cleaned_data['rephotosBy'].get_display_name
            rephotos_by_id = filter_form.cleaned_data['rephotosBy'].pk
            rephotos_by = filter_form.cleaned_data['rephotosBy']
        else:
            rephotos_by = None
        if not album and not requested_photos and not my_likes_only and not rephotos_by \
                and not filter_form.cleaned_data['order1']:
            context['fb_share_photos'] = None
            context['facebook_share_photos'] = None
            context['album'] = None
            context['photo'] = None
            context['page'] = None
            context['user_has_likes'] = None
            context['user_has_rephotos'] = None
            context['my_likes_only'] = None
            context['rephotos_by'] = rephotos_by_id or None
            context['rephotos_by_name'] = rephotos_by_name or None
            context['photos_with_comments'] = None
            context['photos_with_rephotos'] = None
            context['photos_with_similar_photos'] = None
            context['show_photos'] = None
            context['is_photoset'] = None
            context['execution_time'] = str(time() - starttime)
            return context
        else:
            show_photos = True
        lat = filter_form.cleaned_data['lat']
        lon = filter_form.cleaned_data['lon']
        if page_override:
            page = int(page_override)
        else:
            page = filter_form.cleaned_data['page']

        # Do not show hidden photos
        if not album or album.id != 38516:
            blacklist_exists = Album.objects.filter(id=38516).exists()
            if blacklist_exists:
                photos = photos.exclude(albums__in = [38516])

        # FILTERING BELOW THIS LINE

        if album:
            sa_ids = [album.id]
            for sa in album.subalbums.exclude(atype=Album.AUTO):
                sa_ids.append(sa.id)
            photos = photos.filter(albums__in = sa_ids)

            # In QuerySet "albums__in" is 1:M JOIN  so images will show up
            # multiple times in results so this needs to be distinct(). Distinct is slow.
            photos=photos.distinct()

        if filter_form.cleaned_data['people']:
            photos = photos.filter(face_recognition_rectangles__isnull=False,
                                   face_recognition_rectangles__deleted__isnull=True)
        if filter_form.cleaned_data['backsides']:
            photos = photos.filter(front_of__isnull=False)
        if filter_form.cleaned_data['interiors']:
            photos = photos.filter(scene=0)
        if filter_form.cleaned_data['exteriors']:
            photos = photos.exclude(scene=0)
        if filter_form.cleaned_data['ground_viewpoint_elevation']:
            photos = photos.exclude(viewpoint_elevation=1).exclude(viewpoint_elevation=2)
        if filter_form.cleaned_data['raised_viewpoint_elevation']:
            photos = photos.filter(viewpoint_elevation=1)
        if filter_form.cleaned_data['aerial_viewpoint_elevation']:
            photos = photos.filter(viewpoint_elevation=2)
        if filter_form.cleaned_data['no_geotags']:
            photos = photos.filter(geotag_count=0)
        if filter_form.cleaned_data['high_quality']:
            photos = photos.filter(height__gte=1080)
        if filter_form.cleaned_data['portrait']:
            photos = photos.filter(aspect_ratio__lt=0.95)
        if filter_form.cleaned_data['square']:
            photos = photos.filter(aspect_ratio__gte=0.95, aspect_ratio__lt=1.05)
        if filter_form.cleaned_data['landscape']:
            photos = photos.filter(aspect_ratio__gte=1.05, aspect_ratio__lt=2.0)
        if filter_form.cleaned_data['panoramic']:
            photos = photos.filter(aspect_ratio__gte=2.0)
        if requested_photos:
            requested_photos = requested_photos.split(',')
            context['is_photoset'] = True
            photos = photos.filter(id__in=requested_photos)
        else:
            context['is_photoset'] = False
        if my_likes_only:
            photos = photos.filter(likes__profile=profile)
        if rephotos_by_id:
            photos = photos.filter(rephotos__user_id=rephotos_by_id)
        photos_with_comments = None
        photos_with_rephotos = None
        photos_with_similar_photos = None
        q = filter_form.cleaned_data['q']
        if q and show_photos:
            sqs = SearchQuerySet().models(Photo).filter(content=AutoQuery(q))
            photos = photos.filter(pk__in=[r.pk for r in sqs], rephoto_of__isnull=True)

        # In some cases it is faster to get number of photos before we annotate new columns to it
        albumsize_before_sorting = 0
        if not album:
            albumsize_before_sorting=Photo.objects.filter(pk__in=photos).cached_count()

        # SORTING BELOW THIS LINE

        if order1 == 'closest' and lat and lon:
            ref_location = Point(x=lon, y=lat, srid=4326)
            if order3 == 'reverse':
                photos = photos.annotate(distance=GeometryDistance(('geography'), ref_location)).order_by('-distance')
            else:
                photos = photos.annotate(distance=GeometryDistance(('geography'), ref_location)).order_by('distance')
        elif order1 == 'amount':
            if order2 == 'comments':
                if order3 == 'reverse':
                    photos = photos.order_by('comment_count')
                else:
                    photos = photos.order_by('-comment_count')
                photos_with_comments = photos.filter(comment_count__gt=0).count()
            elif order2 == 'rephotos':
                if order3 == 'reverse':
                    photos = photos.order_by('rephoto_count')
                else:
                    photos = photos.order_by('-rephoto_count')
                photos_with_rephotos = photos.filter(rephoto_count__gt=0).count()
            elif order2 == 'geotags':
                if order3 == 'reverse':
                    photos = photos.order_by('geotag_count')
                else:
                    photos = photos.order_by('-geotag_count')
            elif order2 == 'likes':
                if order3 == 'reverse':
                    photos = photos.order_by('like_count')
                else:
                    photos = photos.order_by('-like_count')
            elif order2 == 'views':
                if order3 == 'reverse':
                    photos = photos.order_by('view_count')
                else:
                    photos = photos.order_by('-view_count')
            elif order2 == 'datings':
                if order3 == 'reverse':
                    photos = photos.order_by('dating_count')
                else:
                    photos = photos.order_by('-dating_count')
            elif order2 == 'transcriptions':
                if order3 == 'reverse':
                    photos = photos.order_by('transcription_count')
                else:
                    photos = photos.order_by('-transcription_count')
            elif order2 == 'annotations':
                if order3 == 'reverse':
                    photos = photos.order_by('annotation_count')
                else:
                    photos = photos.order_by('-annotation_count')
            elif order2 == 'similar_photos':
                photos = photos.annotate(similar_photo_count=Count('similar_photos', distinct=True))
                if order3 == 'reverse':
                    photos = photos.order_by('similar_photo_count')
                else:
                    photos = photos.order_by('-similar_photo_count')
        elif order1 == 'time':
            if order2 == 'rephotos':
                if order3 == 'reverse':
                    photos = photos.order_by(F('first_rephoto').asc(nulls_last=True))
                else:
                    photos = photos.order_by(F('latest_rephoto').desc(nulls_last=True))
                photos_with_rephotos = photos.filter(first_rephoto__isnull=False).count()
            elif order2 == 'comments':
                if order3 == 'reverse':
                    photos = photos.order_by(F('first_comment').asc(nulls_last=True))
                else:
                    photos = photos.order_by(F('latest_comment').desc(nulls_last=True))
                photos_with_comments = photos.filter(comment_count__gt=0).count()
            elif order2 == 'geotags':
                if order3 == 'reverse':
                    photos = photos.order_by(F('first_geotag').asc(nulls_last=True))
                else:
                    photos = photos.order_by(F('latest_geotag').desc(nulls_last=True))
            elif order2 == 'likes':
                if order3 == 'reverse':
                    photos = photos.order_by(F('first_like').asc(nulls_last=True))
                else:
                    photos = photos.order_by(F('latest_like').desc(nulls_last=True))
            elif order2 == 'views':
                if order3 == 'reverse':
                    photos = photos.order_by(F('first_view').asc(nulls_last=True))
                else:
                    photos = photos.order_by(F('latest_view').desc(nulls_last=True))
            elif order2 == 'datings':
                if order3 == 'reverse':
                    photos = photos.order_by(F('first_dating').asc(nulls_last=True))
                else:
                    photos = photos.order_by(F('latest_dating').desc(nulls_last=True))
            elif order2 == 'transcriptions':
                if order3 == 'reverse':
                    photos = photos.order_by(F('first_transcription').asc(nulls_last=True))
                else:
                    photos = photos.order_by(F('latest_transcription').desc(nulls_last=True))
            elif order2 == 'annotations':
                if order3 == 'reverse':
                    photos = photos.order_by(F('first_annotation').asc(nulls_last=True))
                else:
                    photos = photos.order_by(F('latest_annotation').desc(nulls_last=True))
            elif order2 == 'stills':
                if order3 == 'reverse':
                    photos = photos.order_by('-video_timestamp')
                else:
                    photos = photos.order_by('video_timestamp')
            elif order2 == 'added':
                if order3 == 'reverse':
                    photos = photos.order_by('id')
                else:
                    photos = photos.order_by('-id')
                if order1 == 'time':
                    default_ordering = True
            elif order2 == 'similar_photos':
                photos = photos.annotate(similar_photo_count=Count('similar_photos', distinct=True))
                if order3 == 'reverse':
                    photos = photos.order_by('similar_photo_count')
                else:
                    photos = photos.order_by('-similar_photo_count')
        else:
            if order3 == 'reverse':
                photos = photos.order_by('id')
            else:
                photos = photos.order_by('-id')
        if not filter_form.cleaned_data['backsides'] and not order2 == 'transcriptions':
            photos = photos.filter(back_of__isnull=True)

# FIXME: values aren't used
# idea is to show page where the selected photo is
# Warning: all photos is very slow
#
#        if requested_photo:
#            ids = list(photos.values_list('id', flat=True))
#            if requested_photo.id in ids:
#                photo_count_before_requested = ids.index(requested_photo.id)
#                page = ceil(float(photo_count_before_requested) / float(page_size))

        # Note seeking (start:end) has been here done when results are limited using photo_ids above
        if albumsize_before_sorting:
            start, end, total, max_page, page = get_pagination_parameters(page, page_size, albumsize_before_sorting)
            # limit QuerySet to selected photos so it is faster to evaluate in next steps
            photos_ids = list(photos.values_list('id', flat=True)[start:end])
            photos=photos.filter(id__in=photos_ids)
        else:
            photos_ids = list(photos.values_list('id', flat=True))
            start, end, total, max_page, page = get_pagination_parameters(page, page_size, len(photos_ids))
            # limit QuerySet to selected photos so it is faster to evaluate in next steps
            photos=photos.filter(id__in=photos_ids[start:end])

        # FIXME: Stupid
        if order1 == 'closest' and lat and lon:
            photos = photos.values_list('id', 'width', 'height', 'description', 'lat', 'lon', 'azimuth',
                                        'rephoto_count', 'comment_count', 'geotag_count', 'distance',
                                        'geotag_count', 'flip', 'has_similar', 'title', 'muis_title',
                                        'muis_comment', 'muis_event_description_set_note', 'geotag_count')
        else:
            photos = photos.values_list('id', 'width', 'height', 'description', 'lat', 'lon', 'azimuth',
                                        'rephoto_count', 'comment_count', 'geotag_count', 'geotag_count',
                                        'geotag_count', 'flip', 'has_similar', 'title', 'muis_title',
                                        'muis_comment', 'muis_event_description_set_note', 'geotag_count')

        photos = [list(i) for i in photos]
        if default_ordering and album and album.ordered:
            album_photos_links_order = AlbumPhoto.objects.filter(album=album).order_by('pk').values_list('photo_id',
                                                                                                         flat=True)
            for each in album_photos_links_order:
                photos = sorted(photos, key=lambda x: x[0] == each)
        # FIXME: Replacing objects with arrays is not a good idea, the small speed boost isn't worth it
        for p in photos:
            if p[3] is not None and p[3] != "" and p[14] is not None and p[14] != "":
                p[3] = p[14] + (". " if p[14][-1] != "." else " ") + p[
                    3]  # add title to image description if both are present.

            # Failback width/height for photos which imagedata arent saved yet
            if p[1] == '' or p[1] is None:
                p[1] = 400
            if p[2] == '' or p[2] is None:
                p[2] = 400
            if p[3] == '' or p[3] is None:
                p[3] = p[14]
            if p[3] == '' or p[3] is None:
                p[3] = p[15]
            if p[3] == '' or p[3] is None:
                p[3] = p[16]
            if p[3] == '' or p[3] is None:
                p[3] = p[17]
            if p[2] >= 1080:
                p[18] = True
            else:
                p[18] = False
            if hasattr(p[10], 'm'):
                p[10] = p[10].m
            p[1], p[2] = calculate_thumbnail_size(p[1], p[2], 400)
            if 'photo_selection' in request.session:
                p[11] = 1 if str(p[0]) in request.session['photo_selection'] else 0
            else:
                p[11] = 0
            p.append(_get_pseudo_slug_for_photo(p[3], None, p[0]))
        if album:
            context['album'] = (
                album.id,
                album.name,
                ','.join(album.name.split(' ')),
                album.lat,
                album.lon,
                album.is_film_still_album,
                album.get_album_type
            )
            context['videos'] = VideoSerializer(album.videos.all(), many=True).data
        else:
            context['album'] = None
        fb_share_photos = []
        if requested_photo:
            context['photo'] = [
                requested_photo.pk,
                requested_photo.get_pseudo_slug(),
                requested_photo.width,
                requested_photo.height
            ]
            fb_share_photos = [context['photo']]
        else:
            context['photo'] = None
            fb_id_list = [p[0] for p in photos[:5]]
            qs_for_fb = Photo.objects.filter(id__in=fb_id_list)
            for p in qs_for_fb:
                fb_share_photos.append([p.id, p.get_pseudo_slug(), p.width, p.height])
        context['photos'] = photos
        context['show_photos'] = show_photos
        # FIXME: DRY
        context['fb_share_photos'] = fb_share_photos
        context['start'] = start
        context['end'] = end
        context['photos_with_comments'] = photos_with_comments
        context['photos_with_rephotos'] = photos_with_rephotos
        context['photos_with_similar_photos'] = photos_with_similar_photos
        context['page'] = page
        context['total'] = total
        context['max_page'] = max_page
        context['my_likes_only'] = my_likes_only
        context['rephotos_by'] = rephotos_by_id
        context['rephotos_by_name'] = rephotos_by_name
    else:
        context['album'] = None
        context['photo'] = None
        context['photos_with_comments'] = photos.filter(comment_count__isnull=False).count()
        context['photos_with_rephotos'] = photos.filter(rephoto_count__isnull=False).count()
        context['photos_with_similar_photos'] = photos.filter(similar_photos__isnull=False)
        photos = photos.values_list('id', 'width', 'height', 'description', 'lat', 'lon', 'azimuth',
                                    'rephoto_count', 'comment_count', 'geotag_count', 'geotag_count',
                                    'geotag_count', 'title', 'muis_title', 'muis_comment',
                                    'muis_event_description_set_note')[0:page_size]
        fb_share_photos = []
        fb_id_list = [p[0] for p in photos[:5]]
        qs_for_fb = Photo.objects.filter(id__in=fb_id_list)
        for p in qs_for_fb:
            fb_share_photos.append([p.id, p.get_pseudo_slug(), p.width, p.height])
        context['fb_share_photos'] = fb_share_photos
        context['order1'] = 'time'
        context['order2'] = 'added'
        context['order3'] = ''
        context['is_photoset'] = False
        context['my_likes_only'] = False
        context['rephotos_by'] = None
        context['rephotos_by_name'] = None
        context['total'] = photos.count()
        photos = [list(each) for each in photos]
        for p in photos:
            p[1], p[2] = calculate_thumbnail_size(p[1], p[2], 400)
            if 'photo_selection' in request.session:
                p[11] = 1 if str(p[0]) in request.session['photo_selection'] else 0
            else:
                p[11] = 0
        context['photos'] = photos
        context['start'] = 0
        context['end'] = page_size
        context['page'] = 1
        context['show_photos'] = False
        context['max_page'] = ceil(float(context['total']) / float(page_size))

    context['execution_time'] = str(time() - starttime)
    return context


def photo_selection(request):
    form = PhotoSelectionForm(request.POST)
    if 'photo_selection' not in request.session:
        request.session['photo_selection'] = {}
    if form.is_valid():
        if form.cleaned_data['clear']:
            request.session['photo_selection'] = {}
        elif form.cleaned_data['id']:
            photo_id = str(form.cleaned_data['id'].id)
            helper = request.session['photo_selection']
            if photo_id not in request.session['photo_selection']:
                helper[photo_id] = True
            else:
                del helper[photo_id]
            request.session['photo_selection'] = helper

    return HttpResponse(json.dumps(request.session['photo_selection']), content_type='application/json')


def list_photo_selection(request):
    photos = None
    at_least_one_photo_has_location = False
    count_with_location = 0
    whole_set_albums_selection_form = CuratorWholeSetAlbumsSelectionForm()
    if 'photo_selection' in request.session:
        photos = Photo.objects.filter(pk__in=request.session['photo_selection']).values_list('id', 'width', 'height',
                                                                                             'flip', 'description',
                                                                                             'lat', 'lon')
        photos = [list(each) for each in photos]
        for p in photos:
            if p[5] and p[6]:
                at_least_one_photo_has_location = True
                count_with_location += 1
            p[1], p[2] = calculate_thumbnail_size_max_height(p[1], p[2], 300)
    context = {
        'is_selection': True,
        'photos': photos,
        'at_least_one_photo_has_location': at_least_one_photo_has_location,
        'count_with_location': count_with_location,
        'whole_set_albums_selection_form': whole_set_albums_selection_form
    }

    return render(request, 'photo/selection/photo_selection.html', context)


def upload_photo_selection(request):
    form = SelectionUploadForm(request.POST)
    context = {
        'ajapaik_facebook_link': settings.AJAPAIK_FACEBOOK_LINK,
        'error': False
    }
    profile = request.get_user().profile
    if form.is_valid() and profile.is_legit():
        albums = Album.objects.filter(id__in=request.POST.getlist('albums'))
        photo_ids = json.loads(form.cleaned_data['selection'])

        if not albums.exists():
            context['error'] = _('Cannot upload to these albums')

        album_photos = []
        points = []
        for a in albums:
            for pid in photo_ids:
                try:
                    p = Photo.objects.get(pk=pid)
                    existing_link = AlbumPhoto.objects.filter(album=a, photo_id=pid).first()
                    if not existing_link:
                        album_photos.append(
                            AlbumPhoto(photo=p,
                                       album=a,
                                       profile=profile,
                                       type=AlbumPhoto.RECURATED
                                       )
                        )
                        points.append(
                            Points(user=profile, action=Points.PHOTO_RECURATION, photo_id=pid, points=30, album=a,
                                   created=timezone.now()))
                except:  # noqa
                    pass
                if a.cover_photo is None and p is not None:
                    a.cover_photo = p

        AlbumPhoto.objects.bulk_create(album_photos)
        Points.objects.bulk_create(points)

        for a in albums:
            a.set_calculated_fields()
            a.light_save()

        profile.set_calculated_fields()
        profile.save()
        context['message'] = _('Recuration successful')
    else:
        context['error'] = _('Faulty data submitted')

    return HttpResponse(json.dumps(context), content_type='application/json')


# FIXME: This should either be used more or not at all
def _make_fullscreen(p):
    if p and p.image:
        return {'url': p.image.url, 'size': [p.image.width, p.image.height]}


def videoslug(request, video_id, pseudo_slug=None):
    video = get_object_or_404(Video, pk=video_id)
    if request.is_ajax():
        template = 'video/_video_modal.html'
    else:
        template = 'video/videoview.html'

    return render(request, template, {'video': video, })


@ensure_csrf_cookie
def photoslug(request, photo_id=None, pseudo_slug=None):
    # Because of some bad design decisions, we have a URL /photo, let's just give a last photo
    if photo_id is None:
        photo_id = Photo.objects.last().pk
    # TODO: Should replace slug with correct one, many thing to keep in mind though:
    #  Google indexing, Facebook shares, comments, likes etc.
    profile = request.get_user().profile
    photo_obj = get_object_or_404(Photo, id=photo_id)

    user_has_likes = profile.likes.exists()
    user_has_rephotos = profile.photos.filter(rephoto_of__isnull=False).exists()

    # switch places if rephoto url
    rephoto = None
    first_rephoto = None
    if hasattr(photo_obj, 'rephoto_of') and photo_obj.rephoto_of is not None:
        rephoto = photo_obj
        photo_obj = photo_obj.rephoto_of

    geotag_count = 0
    azimuth_count = 0
    original_thumb_size = None
    first_geotaggers = []
    if photo_obj:
        original_thumb_size = get_thumbnail(photo_obj.image, '1024x1024').size
        geotags = GeoTag.objects.filter(photo_id=photo_obj.id).distinct('user_id').order_by('user_id', '-created')
        geotag_count = geotags.count()
        if geotag_count > 0:
            correct_geotags_from_authenticated_users = geotags.exclude(user__pk=profile.user_id).filter(
                Q(user__first_name__isnull=False, user__last_name__isnull=False, is_correct=True))[:3]
            if correct_geotags_from_authenticated_users.exists():
                for each in correct_geotags_from_authenticated_users:
                    first_geotaggers.append([each.user.get_display_name, each.lat, each.lon, each.azimuth])
            first_geotaggers = json.dumps(first_geotaggers)
        azimuth_count = geotags.filter(azimuth__isnull=False).count()
        first_rephoto = photo_obj.rephotos.all().first()
        if 'user_view_array' not in request.session:
            request.session['user_view_array'] = []
        if photo_obj.id not in request.session['user_view_array']:
            photo_obj.view_count += 1
        now = timezone.now()
        if not photo_obj.first_view:
            photo_obj.first_view = now
        photo_obj.latest_view = now
        photo_obj.light_save()
        request.session['user_view_array'].append(photo_obj.id)
        request.session.modified = True

    is_frontpage = False
    is_mapview = False
    is_selection = False
    if request.is_ajax():
        template = 'photo/_photo_modal.html'
        if request.GET.get('isFrontpage'):
            is_frontpage = True
        if request.GET.get('isMapview'):
            is_mapview = True
        if request.GET.get('isSelection'):
            is_selection = True
    else:
        template = 'photo/photoview.html'

    if not photo_obj.get_display_text:
        title = 'Unknown photo'
    else:
        title = ' '.join(photo_obj.get_display_text.split(' ')[:5])[:50]

    if photo_obj.author:
        title += f' – {photo_obj.author}'

    album_ids = AlbumPhoto.objects.filter(photo_id=photo_obj.id).values_list('album_id', flat=True)
    full_album_id_list = list(album_ids)
    albums = Album.objects.filter(pk__in=album_ids, atype=Album.CURATED).prefetch_related('subalbum_of')
    collection_albums = Album.objects.filter(pk__in=album_ids, atype=Album.COLLECTION)
    for each in albums:
        if each.subalbum_of:
            current_parent = each.subalbum_of
            while current_parent is not None:
                full_album_id_list.append(current_parent.id)
                current_parent = current_parent.subalbum_of
    albums = Album.objects.filter(pk__in=full_album_id_list, atype=Album.CURATED)
    for a in albums:
        first_albumphoto = AlbumPhoto.objects.filter(photo_id=photo_obj.id, album=a).first()
        if first_albumphoto:
            a.this_photo_curator = first_albumphoto.profile
    album = albums.first()
    next_photo = None
    previous_photo = None
    if album:
        album_selection_form = AlbumSelectionForm({'album': album.id})
        if not request.is_ajax():
            next_photo_id = AlbumPhoto.objects.filter(photo__gt=photo_obj.pk,album=album.id).aggregate(min_id=Min('photo_id'))['min_id']
            if next_photo_id:
                next_photo = Photo.objects.get(pk=next_photo_id)

            previous_photo_id = AlbumPhoto.objects.filter(photo__lt=photo_obj.pk,album=album.id).aggregate(max_id=Max('photo_id'))['max_id']
            if previous_photo_id:
                previous_photo = Photo.objects.get(pk=previous_photo_id)
    else:
        album_selection_form = AlbumSelectionForm(
            initial={'album': Album.objects.filter(is_public=True).order_by('-created').first()}
        )
        if not request.is_ajax():
            next_photo_id = Photo.objects.filter(pk__gt=photo_obj.pk).aggregate(min_id=Min('id'))['min_id']
            if next_photo_id:
                next_photo = Photo.objects.get(pk=next_photo_id)

            previous_photo_id = Photo.objects.filter(pk__lt=photo_obj.pk).aggregate(max_id=Max('id'))['max_id']
            if previous_photo_id:
                previous_photo = Photo.objects.get(pk=previous_photo_id)

    if album:
        album = (album.id, album.lat, album.lon)

    rephoto_fullscreen = None
    if first_rephoto is not None:
        rephoto_fullscreen = _make_fullscreen(first_rephoto)

    if photo_obj and photo_obj.get_display_text:
        photo_obj.tags = ','.join(photo_obj.get_display_text.split(' '))
    if rephoto and rephoto.get_display_text:
        rephoto.tags = ','.join(rephoto.get_display_text.split(' '))

    if 'photo_selection' in request.session:
        if str(photo_obj.id) in request.session['photo_selection']:
            photo_obj.in_selection = True

    user_confirmed_this_location = 'false'
    user_has_geotagged = GeoTag.objects.filter(photo=photo_obj, user=profile).exists()
    if user_has_geotagged:
        user_has_geotagged = 'true'
    else:
        user_has_geotagged = 'false'
    last_user_confirm_geotag_for_this_photo = GeoTag.objects.filter(type=GeoTag.CONFIRMATION, photo=photo_obj,
                                                                    user=profile) \
        .order_by('-created').first()
    if last_user_confirm_geotag_for_this_photo:
        if last_user_confirm_geotag_for_this_photo.lat == photo_obj.lat \
                and last_user_confirm_geotag_for_this_photo.lon == photo_obj.lon:
            user_confirmed_this_location = 'true'

    photo_obj.user_likes = False
    photo_obj.user_loves = False
    likes = PhotoLike.objects.filter(photo=photo_obj)
    photo_obj.like_count = likes.distinct('profile').count()
    like = likes.filter(profile=profile).first()
    if like:
        if like.level == 1:
            photo_obj.user_likes = True
        elif like.level == 2:
            photo_obj.user_loves = True

    previous_datings = photo_obj.datings.order_by('created').prefetch_related('confirmations')
    for each in previous_datings:
        each.this_user_has_confirmed = each.confirmations.filter(profile=profile).exists()
    serialized_datings = DatingSerializer(previous_datings, many=True).data
    serialized_datings = JSONRenderer().render(serialized_datings).decode('utf-8')

    strings = []
    if photo_obj.source:
        strings = [photo_obj.source.description, photo_obj.source_key]
    desc = ' '.join(filter(None, strings))

    next_similar_photo = photo_obj
    if next_photo is not None:
        next_similar_photo = next_photo
    compare_photos_url = request.build_absolute_uri(
        reverse('compare-photos', args=(photo_obj.id, next_similar_photo.id)))
    imageSimilarities = ImageSimilarity.objects.filter(from_photo_id=photo_obj.id).exclude(similarity_type=0)
    if imageSimilarities.exists():
        compare_photos_url = request.build_absolute_uri(
            reverse('compare-photos', args=(photo_obj.id, imageSimilarities.first().to_photo_id)))

    people = [x.name for x in photo_obj.people]
    similar_photos = ImageSimilarity.objects.filter(from_photo=photo_obj.id).exclude(similarity_type=0)

    similar_fullscreen = None
    if similar_photos.all().first() is not None:
        similar_fullscreen = _make_fullscreen(similar_photos.all().first().to_photo)

    whole_set_albums_selection_form = CuratorWholeSetAlbumsSelectionForm()

    reverse_side = None
    if photo_obj.back_of is not None:
        reverse_side = photo_obj.back_of
    elif photo_obj.front_of is not None:
        reverse_side = photo_obj.front_of

    seconds = None
    if photo_obj.video_timestamp:
        seconds = photo_obj.video_timestamp / 1000

    context = {
        'photo': photo_obj,
        'similar_photos': similar_photos,
        'previous_datings': serialized_datings,
        'datings_count': previous_datings.count(),
        'original_thumb_size': original_thumb_size,
        'user_confirmed_this_location': user_confirmed_this_location,
        'user_has_geotagged': user_has_geotagged,
        'fb_url': request.build_absolute_uri(reverse('photo', args=(photo_obj.id,))),
        'licence': Licence.objects.get(id=17),  # CC BY 4.0
        'area': photo_obj.area,
        'album': album,
        'albums': albums,
        'collection_albums': collection_albums,
        'is_frontpage': is_frontpage,
        'is_mapview': is_mapview,
        'is_selection': is_selection,
        'album_selection_form': album_selection_form,
        'geotag_count': geotag_count,
        'azimuth_count': azimuth_count,
        'fullscreen': _make_fullscreen(photo_obj),
        'rephoto_fullscreen': rephoto_fullscreen,
        'similar_fullscreen': similar_fullscreen,
        'title': title,
        'description': desc,
        'rephoto': rephoto,
        'hostname': request.build_absolute_uri('/'),
        'first_geotaggers': first_geotaggers,
        'is_photoview': True,
        'ajapaik_facebook_link': settings.AJAPAIK_FACEBOOK_LINK,
        'user_has_likes': user_has_likes,
        'user_has_rephotos': user_has_rephotos,
        'next_photo': next_photo,
        'previous_photo': previous_photo,
        'similar_photo_count': similar_photos.count(),
        'confirmed_similar_photo_count': similar_photos.filter(confirmed=True).count(),
        'compare_photos_url': compare_photos_url,
        'reverse_side': reverse_side,
        'is_photo_modal': request.is_ajax(),
        # TODO: Needs more data than just the names
        'people': people,
        'whole_set_albums_selection_form': whole_set_albums_selection_form,
        'seconds': seconds
    }

    return render(request, template, context)


def photo_upload_modal(request, photo_id):
    photo = get_object_or_404(Photo, pk=photo_id)
    licence = Licence.objects.get(id=17)  # CC BY 4.0
    context = {
        'photo': photo,
        'licence': licence,
        'next': request.META['HTTP_REFERER']
    }
    return render(request, 'rephoto_upload/_rephoto_upload_modal_content.html', context)


def login_modal(request):
    context = {
        'next': request.META.get('HTTP_REFERER', None),
        'type': request.GET.get('type', None)
    }
    return render(request, 'authentication/_login_modal_content.html', context)


@ensure_csrf_cookie
def mapview(request, photo_id=None, rephoto_id=None):
    profile = request.get_user().profile
    area_selection_form = AreaSelectionForm(request.GET)
    game_album_selection_form = GameAlbumSelectionForm(request.GET)
    albums = _get_album_choices(None, 0, 1)  # Where albums variable is used?
    photos_qs = Photo.objects.filter(rephoto_of__isnull=True).values('id')
    select_all_photos = True

    user_has_likes = profile.likes.exists()
    user_has_rephotos = profile.photos.filter(rephoto_of__isnull=False).exists()

    area = None
    album = None
    if area_selection_form.is_valid():
        select_all_photos = False
        area = area_selection_form.cleaned_data['area']
        photos_qs = photos_qs.filter(area=area)

    if game_album_selection_form.is_valid():
        select_all_photos = False
        album = game_album_selection_form.cleaned_data['album']
        photos_qs = album.photos.prefetch_related('subalbums')
        for sa in album.subalbums.exclude(atype=Album.AUTO):
            photos_qs = photos_qs | sa.photos.filter(rephoto_of__isnull=True)

    selected_photo = None
    selected_rephoto = None
    if rephoto_id:
        selected_rephoto = Photo.objects.filter(pk=rephoto_id).first()

    if photo_id:
        selected_photo = Photo.objects.filter(pk=photo_id).first()
    else:
        if selected_rephoto:
            selected_photo = Photo.objects.filter(pk=selected_rephoto.rephoto_of.id).first()

    if selected_photo and album is None:
        photo_album_ids = AlbumPhoto.objects.filter(photo_id=selected_photo.id).values_list('album_id', flat=True)
        album = Album.objects.filter(pk__in=photo_album_ids, is_public=True).order_by('-created').first()
        if album:
            select_all_photos = False
            photos_qs = album.photos.prefetch_related('subalbums').filter(rephoto_of__isnull=True)
            for sa in album.subalbums.exclude(atype=Album.AUTO):
                photos_qs = photos_qs | sa.photos.filter(rephoto_of__isnull=True)

    if selected_photo and area is None:
        select_all_photos = False
        area = Area.objects.filter(pk=selected_photo.area_id).first()
        photos_qs = photos_qs.filter(area=area, rephoto_of__isnull=True)

    # If we using unfiltered view then we can just count all geotags
    if select_all_photos:
        geotagging_user_count = GeoTag.objects.distinct('user').values('user').count()
        total_photo_count = photos_qs.count()
    else:
        geotagging_user_count = GeoTag.objects.filter(photo_id__in=photos_qs.values_list('id', flat=True)).distinct(
            'user').values('user').count()
        total_photo_count = photos_qs.distinct('id').values('id').count()

    geotagged_photo_count = photos_qs.distinct('id').filter(lat__isnull=False, lon__isnull=False).count()

    if geotagged_photo_count:
        last_geotagged_photo_id = Photo.objects.order_by(F('latest_geotag').desc(nulls_last=True)).values('id').first()['id']
    else:
        last_geotagged_photo_id = None

    context = {'area': area, 'last_geotagged_photo_id': last_geotagged_photo_id,
               'total_photo_count': total_photo_count, 'geotagging_user_count': geotagging_user_count,
               'geotagged_photo_count': geotagged_photo_count, 'albums': albums,
               'hostname': request.build_absolute_uri('/'),
               'selected_photo': selected_photo, 'selected_rephoto': selected_rephoto, 'is_mapview': True,
               'ajapaik_facebook_link': settings.AJAPAIK_FACEBOOK_LINK, 'album': None, 'user_has_likes': user_has_likes,
               'user_has_rephotos': user_has_rephotos, 'query_string': request.GET.get('q')}

    if album is not None:
        context['album'] = (album.id, album.name, album.lat, album.lon, ','.join(album.name.split(' ')))
        context['title'] = f'{album.name} - {_("Browse photos on map")}'
        context['facebook_share_photos'] = []
        facebook_share_photos = album.photos.all()[:5]
        for each in facebook_share_photos:
            each = [each.pk, each.get_pseudo_slug(), each.width, each.height]
            context['facebook_share_photos'].append(each)
    elif area is not None:
        context['title'] = f'{area.name} - {_("Browse photos on map")}'
    else:
        context['title'] = _('Browse photos on map')
    context['show_photos'] = True
    return render(request, 'common/mapview.html', context)


def map_objects_by_bounding_box(request):
    form = MapDataRequestForm(request.POST)

    if form.is_valid():
        album = form.cleaned_data['album']
        area = form.cleaned_data['area']
        limit_by_album = form.cleaned_data['limit_by_album']
        sw_lat = form.cleaned_data['sw_lat']
        sw_lon = form.cleaned_data['sw_lon']
        ne_lat = form.cleaned_data['ne_lat']
        ne_lon = form.cleaned_data['ne_lon']
        count_limit = form.cleaned_data['count_limit']
        query_string = form.cleaned_data['query_string']

        qs = Photo.objects.filter(
            lat__isnull=False, lon__isnull=False, rephoto_of__isnull=True
        )

        if album and limit_by_album:
            album_photo_ids = album.get_historic_photos_queryset_with_subalbums().values_list('id', flat=True)
            qs = qs.filter(id__in=album_photo_ids)

        if area:
            qs = qs.filter(area=area)

        if sw_lat and sw_lon and ne_lat and ne_lon:
            qs = qs.filter(lat__gte=sw_lat, lon__gte=sw_lon, lat__lte=ne_lat, lon__lte=ne_lon)

        if query_string:
            qs = qs.filter(Q(description_et__icontains=query_string) | Q(description_fi__icontains=query_string) | Q(
                description_sv__icontains=query_string) | Q(description_nl__icontains=query_string) | Q(
                description_lv__icontains=query_string) | Q(description_lt__icontains=query_string) | Q(
                description_de__icontains=query_string) | Q(description_ru__icontains=query_string) | Q(
                author__icontains=query_string) | Q(types__icontains=query_string) | Q(
                keywords__icontains=query_string) | Q(source_key__icontains=query_string) | Q(
                source__name__icontains=query_string) | Q(address__icontains=query_string))

        if count_limit:
            qs = qs.order_by('?')[:count_limit]

        data = {
            'photos': PhotoMapMarkerSerializer(
                qs,
                many=True,
                photo_selection=request.session.get('photo_selection', [])
            ).data
        }
    else:
        data = {
            'photos': []
        }

    return JsonResponse(data)


def geotag_add(request):
    submit_geotag_form = SubmitGeotagForm(request.POST)
    profile = request.get_user().profile
    flip_points = 0
    flip_response = ''
    was_flip_successful = None
    context = {}
    if submit_geotag_form.is_valid():
        azimuth_score = 0
        new_geotag = submit_geotag_form.save(commit=False)
        new_geotag.user = profile
        trust = _calc_trustworthiness(profile.id)
        new_geotag.trustworthiness = trust
        tagged_photo = submit_geotag_form.cleaned_data['photo']
        # user flips, photo is flipped -> flip back
        # user flips, photo isn't flipped -> flip
        # user doesn't flip, photo is flipped -> leave flipped
        # user doesn't flip, photo isn't flipped -> leave as is
        if new_geotag.photo_flipped:
            original_photo = Photo.objects.filter(id=tagged_photo.id).first()
            flip_response, flip_suggestions, was_flip_successful, flip_points = suggest_photo_edit(
                [],
                'flip',
                not original_photo.flip,
                Points,
                40,
                Points.FLIP_PHOTO,
                PhotoFlipSuggestion,
                tagged_photo,
                profile,
                flip_response,
                'do_flip'
            )
            PhotoFlipSuggestion.objects.bulk_create(flip_suggestions)
        new_geotag.save()
        initial_lat = tagged_photo.lat
        initial_lon = tagged_photo.lon
        # Calculate new lat, lon, confidence, suggestion_level, azimuth, azimuth_confidence, geotag_count for photo
        tagged_photo.set_calculated_fields()
        tagged_photo.latest_geotag = timezone.now()
        tagged_photo.save()
        processed_tagged_photo = Photo.objects.filter(pk=tagged_photo.id).get()
        context['estimated_location'] = [processed_tagged_photo.lat, processed_tagged_photo.lon]
        if processed_tagged_photo.azimuth:
            context['azimuth'] = processed_tagged_photo.azimuth
        processed_geotag = GeoTag.objects.filter(pk=new_geotag.id).get()
        if processed_geotag.origin == GeoTag.GAME:
            if len(tagged_photo.geotags.all()) == 1:
                score = max(20, int(300 * trust))
            else:
                # TODO: How bulletproof is this? 0 score geotags happen then and again
                try:
                    error_in_meters = distance_in_meters(tagged_photo.lon, tagged_photo.lat, processed_geotag.lon,
                                                         processed_geotag.lat)
                    score = int(130 * max(0, min(1, (1 - (error_in_meters - 15) / float(94 - 15)))))
                except TypeError:
                    score = 0
        else:
            score = int(trust * 100)
        if processed_geotag.hint_used:
            score *= 0.75
        if processed_geotag.azimuth_correct and tagged_photo.azimuth and processed_geotag.azimuth:
            degree_error_point_array = [100, 99, 97, 93, 87, 83, 79, 73, 67, 61, 55, 46, 37, 28, 19, 10]
            difference = int(angle_diff(tagged_photo.azimuth, processed_geotag.azimuth))
            if difference <= 15:
                azimuth_score = degree_error_point_array[int(difference)]
        processed_geotag.azimuth_score = azimuth_score
        processed_geotag.score = score + azimuth_score
        processed_geotag.save()
        context['current_score'] = processed_geotag.score + flip_points
        Points(user=profile, action=Points.GEOTAG, geotag=processed_geotag, points=processed_geotag.score,
               created=timezone.now(), photo=processed_geotag.photo).save()
        geotags_for_this_photo = GeoTag.objects.filter(photo=tagged_photo)
        context['new_geotag_count'] = geotags_for_this_photo.distinct('user').count()
        context['heatmap_points'] = [[x.lat, x.lon] for x in geotags_for_this_photo]
        profile.set_calculated_fields()
        profile.save()
        context['feedback_message'] = ''
        processed_photo = Photo.objects.filter(pk=tagged_photo.pk).first()
        if processed_geotag.origin == GeoTag.GAME and processed_photo:
            if processed_photo.lat == initial_lat and processed_photo.lon == initial_lon:
                context['feedback_message'] = _(
                    "Your contribution didn't change the estimated location for the photo, not yet anyway.")
            else:
                context['feedback_message'] = _('The photo has been mapped to a new location thanks to you.')
            if geotags_for_this_photo.count() == 1:
                context['feedback_message'] = _('Your suggestion was first.')
        for a in processed_photo.albums.all():
            qs = a.get_geotagged_historic_photo_queryset_with_subalbums()
            a.geotagged_photo_count_with_subalbums = qs.count()
            a.light_save()
    else:
        if 'lat' not in submit_geotag_form.cleaned_data and 'lon' not in submit_geotag_form.cleaned_data \
                and 'photo_id' in submit_geotag_form.data:
            Skip(user=profile, photo_id=submit_geotag_form.data['photo_id']).save()
            if 'user_skip_array' not in request.session:
                request.session['user_skip_array'] = []
            request.session['user_skip_array'].append(submit_geotag_form.data['photo_id'])
            request.session.modified = True

    context['was_flip_successful'] = was_flip_successful
    context['flip_response'] = flip_response

    return HttpResponse(json.dumps(context), content_type='application/json')


def geotag_confirm(request):
    form = ConfirmGeotagForm(request.POST)
    profile = request.get_user().profile
    context = {
        'message': 'OK'
    }
    if form.is_valid():
        p = form.cleaned_data['photo']
        # Check if user is eligible to confirm location (again)
        last_confirm_geotag_by_this_user_for_p = p.geotags.filter(user_id=profile.id, type=GeoTag.CONFIRMATION) \
            .order_by('-created').first()
        if not last_confirm_geotag_by_this_user_for_p or (p.lat and p.lon and (
                last_confirm_geotag_by_this_user_for_p.lat != p.lat
                and last_confirm_geotag_by_this_user_for_p.lon != p.lon)):
            trust = _calc_trustworthiness(request.get_user().id)
            confirmed_geotag = GeoTag(
                lat=p.lat,
                lon=p.lon,
                origin=GeoTag.MAP_VIEW,
                type=GeoTag.CONFIRMATION,
                map_type=GeoTag.OPEN_STREETMAP,
                hint_used=True,
                user=profile,
                photo=p,
                is_correct=True,
                score=max(1, int(trust * 50)),
                azimuth_score=0,
                trustworthiness=trust
            )
            if p.azimuth:
                confirmed_geotag.azimuth = p.azimuth
                confirmed_geotag.azimuth_correct = True
            confirmed_geotag.save()
            Points(user=profile, action=Points.GEOTAG, geotag=confirmed_geotag, points=confirmed_geotag.score,
                   created=timezone.now(), photo=p).save()
            p.latest_geotag = timezone.now()
            p.save()
            profile.set_calculated_fields()
            profile.save()
        context['new_geotag_count'] = GeoTag.objects.filter(photo=p).distinct('user').count()

    return HttpResponse(json.dumps(context), content_type='application/json')


def leaderboard(request, album_id=None):
    # Leader-board with first position, one in front of you, your score and one after you
    album_leaderboard = None
    general_leaderboard = None
    profile = request.get_user().profile
    if album_id:
        # Album leader-board takes into account any users that have any contributions to the album
        album = get_object_or_404(Album, pk=album_id)
        # TODO: Almost identical code is used in many places, put under album model
        album_photos_qs = album.photos.all()
        for sa in album.subalbums.exclude(atype=Album.AUTO):
            album_photos_qs = album_photos_qs | sa.photos.all()
        album_photo_ids = set(album_photos_qs.values_list('id', flat=True))
        album_rephoto_ids = frozenset(album_photos_qs.filter(rephoto_of__isnull=False)
                                      .values_list('rephoto_of_id', flat=True))
        photo_points = Points.objects.filter(
            Q(photo_id__in=album_photo_ids) | Q(photo_id__in=album_rephoto_ids)).exclude(
            action=Points.PHOTO_RECURATION)
        photo_points = photo_points | Points.objects.filter(photo_id__in=album_photo_ids, album=album,
                                                            action=Points.PHOTO_RECURATION)
        user_score_map = {}
        for each in photo_points:
            if each.user_id in user_score_map:
                user_score_map[each.user_id] += each.points
            else:
                user_score_map[each.user_id] = each.points
        if profile.id not in user_score_map:
            user_score_map[profile.id] = 0
        sorted_scores = sorted(user_score_map.items(), key=operator.itemgetter(1), reverse=True)
        pk_list = [x[0] for x in sorted_scores]
        current_user_rank = pk_list.index(profile.id)
        if current_user_rank == -1:
            current_user_rank = len(sorted_scores)
        current_user_rank += 1
        # Works on Postgres, we don't really need to worry about this I guess...maybe only if it gets slow
        clauses = ' '.join(['WHEN user_id=%s THEN %s' % (pk, i) for i, pk in enumerate(pk_list)])
        ordering = 'CASE %s END' % clauses
        top_users = Profile.objects.filter(Q(user_id__in=pk_list) | Q(user_id=profile.id)) \
            .extra(select={'ordering': ordering}, order_by=('ordering',))
        start = current_user_rank - 2
        if start < 0:
            start = 0
        top_users = top_users[start:current_user_rank + 1]
        n = current_user_rank
        for each in top_users:
            if each.user_id == profile.id:
                each.is_current_user = True
            each.position = n
            each.custom_score = user_score_map[each.user_id]
            n += 1
        album_leaderboard = top_users
    else:
        _calculate_recent_activity_scores()
        profile_rank = Profile.objects.filter(score_recent_activity__gt=profile.score_recent_activity,
                                              first_name__isnull=False, last_name__isnull=False).count() + 1
        leaderboard_queryset = Profile.objects.filter(
            Q(first_name__isnull=False, last_name__isnull=False, score_recent_activity__gt=0) |
            Q(pk=profile.id)).order_by('-score_recent_activity')
        start = profile_rank - 2
        if start < 0:
            start = 0
        nearby_users = leaderboard_queryset[start:profile_rank + 1]
        n = start + 1
        for each in nearby_users:
            if each.user_id == profile.id:
                each.is_current_user = True
            each.position = n
            n += 1
        general_leaderboard = nearby_users
    if request.is_ajax():
        template = 'leaderboard/_block_leaderboard.html'
    else:
        template = 'leaderboard/leaderboard.html'
    # FIXME: this shouldn't be necessary, there are easier ways to construct URLs
    context = {
        'is_top_50': False,
        'title': _('Leaderboard'),
        'hostname': request.build_absolute_uri('/'),
        'leaderboard': general_leaderboard,
        'album_leaderboard': album_leaderboard,
        'ajapaik_facebook_link': settings.AJAPAIK_FACEBOOK_LINK
    }
    return render(request, template, context)


def all_time_leaderboard(request):
    _calculate_recent_activity_scores()
    atl = _get_all_time_leaderboard50(request.get_user().profile.pk)
    template = ['', 'leaderboard/_block_leaderboard.html', 'leaderboard/leaderboard.html'][request.is_ajax() and 1 or 2]
    context = {
        'hostname': request.build_absolute_uri('/'),
        'all_time_leaderboard': atl,
        'title': _('Leaderboard'),
        'is_top_50': True
    }
    return render(request, template, context)


def top50(request, album_id=None):
    _calculate_recent_activity_scores()
    profile = request.get_user().profile
    album_name = None
    album_leaderboard = None
    general_leaderboard = None
    if album_id:
        album_leaderboard, album_name = _get_album_leaderboard50(profile.pk, album_id)
    else:
        general_leaderboard = _get_all_time_leaderboard50(profile.pk)
    activity_leaderboard = Profile.objects.filter(
        Q(first_name__isnull=False, last_name__isnull=False, score_recent_activity__gt=0) |
        Q(pk=profile.id)).order_by('-score_recent_activity').prefetch_related('user')[:50]
    n = 1
    for each in activity_leaderboard:
        if each.user_id == profile.id:
            each.is_current_user = True
        each.position = n
        n += 1
    if request.is_ajax():
        template = 'leaderboard/_block_leaderboard.html'
    else:
        template = 'leaderboard/leaderboard.html'
    context = {
        'activity_leaderboard': activity_leaderboard,
        'album_name': album_name,
        'album_leaderboard': album_leaderboard,
        'all_time_leaderboard': general_leaderboard,
        'hostname': request.build_absolute_uri('/'),
        'title': _('Leaderboard'),
        'is_top_50': True,
        'ajapaik_facebook_link': settings.AJAPAIK_FACEBOOK_LINK
    }
    return render(request, template, context)


def difficulty_feedback(request):
    user_profile = request.get_user().profile
    # FIXME: Form, better error handling
    if not user_profile:
        return HttpResponse('Error', status=500)
    user_trustworthiness = _calc_trustworthiness(user_profile.pk)
    user_last_geotag = GeoTag.objects.filter(user=user_profile).order_by('-created').first()
    level = request.POST.get('level') or None
    photo_id = request.POST.get('photo_id') or None
    # FIXME: Why so many lines?
    if user_profile and level and photo_id and user_last_geotag:
        feedback_object = DifficultyFeedback()
        feedback_object.user_profile = user_profile
        feedback_object.level = level
        feedback_object.photo_id = photo_id
        feedback_object.trustworthiness = user_trustworthiness
        feedback_object.geotag = user_last_geotag
        feedback_object.save()
        photo = Photo.objects.get(id=photo_id)
        # FIXME: Shouldn't use costly set_calculated_fields here, maybe send extra var to lighten it
        photo.set_calculated_fields()
        photo.save()

    return HttpResponse('OK')


def public_add_album(request):
    # FIXME: ModelForm
    add_album_form = AddAlbumForm(request.POST)
    if add_album_form.is_valid():
        user_profile = request.get_user().profile
        name = add_album_form.cleaned_data['name']
        description = add_album_form.cleaned_data['description']
        if user_profile:
            new_album = Album(
                name=name, description=description, atype=Album.COLLECTION, profile=user_profile, is_public=False)
            new_album.save()
            selectable_albums = Album.objects.filter(Q(atype=Album.FRONTPAGE) | Q(profile=user_profile))
            selectable_albums = [{'id': x.id, 'name': x.name} for x in selectable_albums]
            return HttpResponse(json.dumps(selectable_albums), content_type='application/json')
    return HttpResponse(json.dumps('Error'), content_type='application/json', status=400)


def public_add_area(request):
    add_area_form = AddAreaForm(request.POST)
    # TODO: Better duplicate handling
    if add_area_form.is_valid():
        try:
            Area.objects.get(name=add_area_form.cleaned_data['name'])
        except ObjectDoesNotExist:
            user_profile = request.get_user().profile
            name = add_area_form.cleaned_data['name']
            lat = add_area_form.cleaned_data['lat']
            lon = add_area_form.cleaned_data['lon']
            if user_profile:
                new_area = Area(name=name, lat=lat, lon=lon)
                new_area.save()
                selectable_areas = Area.objects.order_by('name').all()
                selectable_areas = [{'id': x.id, 'name': x.name} for x in selectable_areas]
                return HttpResponse(json.dumps(selectable_areas), content_type='application/json')
    return HttpResponse(json.dumps('Error'), content_type='application/json', status=400)


@ensure_csrf_cookie
def curator(request):
    last_created_album = Album.objects.filter(is_public=True).order_by('-created').first()
    # FIXME: Ugly
    curator_random_image_ids = None
    if last_created_album:
        curator_random_image_ids = AlbumPhoto.objects.filter(
            album_id=last_created_album.id).order_by('?').values_list('photo_id', flat=True)
    if not curator_random_image_ids or curator_random_image_ids.count() < 5:
        curator_random_image_ids = AlbumPhoto.objects.order_by('?').values_list('photo_id', flat=True)
    curator_random_images = Photo.objects.filter(pk__in=curator_random_image_ids)[:5]
    context = {
        'description': _('Search for old photos, add them to Ajapaik, '
                         'determine their locations and share the resulting album!'),
        'curator_random_images': curator_random_images,
        'hostname': request.build_absolute_uri('/'),
        'is_curator': True,
        'CURATOR_FLICKR_ENABLED': settings.CURATOR_FLICKR_ENABLED,
        'CURATOR_EUROPEANA_ENABLED': settings.CURATOR_EUROPEANA_ENABLED,
        'ajapaik_facebook_link': settings.AJAPAIK_FACEBOOK_LINK,
        'whole_set_albums_selection_form': CuratorWholeSetAlbumsSelectionForm()
    }

    return render(request, 'curator/curator.html', context)


def extract_values_from_dictionary_to_result(dictionary: dict, result: dict):
    try:
        if 'result' in dictionary:
            for each in dictionary['result']['firstRecordViews']:
                result['firstRecordViews'].append(each)
            if 'page' in dictionary['result']:
                result['page'] = dictionary['result']['page']
            if 'pages' in dictionary['result']:
                result['pages'] = dictionary['result']['pages']
            if 'ids' in dictionary['result']:
                result['ids'] = dictionary['result']['ids']
    except TypeError:
        print('Could not extract values from dictionary', file=sys.stderr)

    return result


def _join_2_json_objects(obj1, obj2):
    result = {'firstRecordViews': []}
    # TODO: Why do errors sometimes happen here?
    try:
        result = extract_values_from_dictionary_to_result(json.loads(obj1), result)
        result = extract_values_from_dictionary_to_result(json.loads(obj2), result)
    except TypeError:
        print('Could not extract values from dictionary', file=sys.stderr)

    return json.dumps({'result': result})


def curator_search(request):
    form = CuratorSearchForm(request.POST)
    response = json.dumps({})
    flickr_driver = None
    valimimoodul_driver = None
    finna_driver = None
    commons_driver = None
    europeana_driver = None
    fotis_driver = None
    if form.is_valid():
        if form.cleaned_data['useFlickr']:
            flickr_driver = FlickrCommonsDriver()
        if form.cleaned_data['useMUIS'] or form.cleaned_data['useMKA'] or form.cleaned_data['useDIGAR'] or \
                form.cleaned_data['useETERA'] or form.cleaned_data['useUTLIB']:
            valimimoodul_driver = ValimimoodulDriver()
            if form.cleaned_data['ids']:
                response = valimimoodul_driver.transform_response(
                    valimimoodul_driver.get_by_ids(form.cleaned_data['ids']),
                    form.cleaned_data['filterExisting'])
        if form.cleaned_data['useCommons']:
            commons_driver = CommonsDriver()
        if form.cleaned_data['useFinna']:
            finna_driver = FinnaDriver()
        if form.cleaned_data['useEuropeana']:
            europeana_driver = EuropeanaDriver()
        if form.cleaned_data['useFotis']:
            fotis_driver = FotisDriver()
        if form.cleaned_data['fullSearch']:
            if valimimoodul_driver and not form.cleaned_data['ids']:
                response = _join_2_json_objects(response, valimimoodul_driver.transform_response(
                    valimimoodul_driver.search(form.cleaned_data), form.cleaned_data['filterExisting']))
            if flickr_driver:
                response = _join_2_json_objects(response, flickr_driver.transform_response(
                    flickr_driver.search(form.cleaned_data), form.cleaned_data['filterExisting']))
            if commons_driver:
                response = _join_2_json_objects(response, commons_driver.transform_response(
                    commons_driver.search(form.cleaned_data), form.cleaned_data['filterExisting']))
            if europeana_driver:
                response = _join_2_json_objects(response, europeana_driver.transform_response(
                    europeana_driver.search(form.cleaned_data), form.cleaned_data['filterExisting']))
            if finna_driver:
                response = _join_2_json_objects(response, finna_driver.transform_response(
                    finna_driver.search(form.cleaned_data), form.cleaned_data['filterExisting'],
                    form.cleaned_data['flickrPage']))
            if fotis_driver:
                response = _join_2_json_objects(response, fotis_driver.transform_response(
                    fotis_driver.search(form.cleaned_data), form.cleaned_data['filterExisting'],
                    form.cleaned_data['flickrPage']))

    return HttpResponse(response, content_type='application/json')


def curator_my_album_list(request):
    user_profile = request.get_user().profile
    serializer = CuratorMyAlbumListAlbumSerializer(
        Album.objects.filter(Q(profile=user_profile, atype__in=[Album.CURATED, Album.PERSON])).order_by('-created'),
        many=True
    )

    return HttpResponse(JSONRenderer().render(serializer.data), content_type='application/json')


def curator_selectable_albums(request):
    user_profile = request.get_user().profile
    serializer = CuratorAlbumSelectionAlbumSerializer(
        Album.objects.filter(((Q(profile=user_profile) | Q(is_public=True)) & ~Q(atype=Album.AUTO)) | (
                Q(open=True) & ~Q(atype=Album.AUTO))).order_by('name').all(), many=True
    )

    return HttpResponse(JSONRenderer().render(serializer.data), content_type='application/json')


# TODO: Replace with Django REST API
def curator_get_album_info(request):
    album_id = request.POST.get('albumId') or None
    if album_id is not None:
        try:
            album = Album.objects.get(pk=album_id)
            serializer = CuratorAlbumInfoSerializer(album)
        except ObjectDoesNotExist:
            return HttpResponse('Album does not exist', status=404)
        return HttpResponse(JSONRenderer().render(serializer.data), content_type='application/json')
    return HttpResponse('No album ID', status=500)


# TODO: Replace with Django REST API
def curator_update_my_album(request):
    album_edit_form = CuratorAlbumEditForm(request.POST)
    is_valid = album_edit_form.is_valid()
    album_id = album_edit_form.cleaned_data['album_id']
    user_profile = request.get_user().profile
    if is_valid and album_id and user_profile:
        try:
            album = Album.objects.get(pk=album_id, profile=user_profile)
        except ObjectDoesNotExist:
            return HttpResponse('Album does not exist', status=404)

        album.name = album_edit_form.cleaned_data['name']
        album.description = album_edit_form.cleaned_data['description']
        album.open = album_edit_form.cleaned_data['open']
        album.is_public = album_edit_form.cleaned_data['is_public']

        if album_edit_form.cleaned_data['areaLat'] and album_edit_form.cleaned_data['areaLng']:
            album.lat = album_edit_form.cleaned_data['areaLat']
            album.lon = album_edit_form.cleaned_data['areaLng']

        parent_album_id = album_edit_form.cleaned_data['parent_album_id']
        if parent_album_id:
            try:
                parent_album = Album.objects.exclude(id=album.id).get(
                    Q(profile=user_profile, is_public=True, pk=parent_album_id) | Q(open=True, pk=parent_album_id))
                album.subalbum_of = parent_album
            except ObjectDoesNotExist:
                return HttpResponse("Invalid parent album", status=500)
        else:
            album.subalbum_of = None

        album.save()

        return HttpResponse('OK', status=200)

    return HttpResponse('Faulty data', status=500)


def _get_licence_name_by_url(url):
    title = url
    try:
        html = requests.get(url, {}).text.replace('\n', '')
        title_search = re.search('<title>(.*)</title>', html, re.IGNORECASE)

        if title_search:
            title = title_search.group(1)
            title = unescape(title)
        return title
    except:  # noqa
        return title


def curator_photo_upload_handler(request):
    profile = request.get_user().profile

    etera_token = request.POST.get('eteraToken')

    curator_album_selection_form = CuratorWholeSetAlbumsSelectionForm(request.POST)

    selection_json = request.POST.get('selection') or None
    selection = None
    if selection_json is not None:
        selection = json.loads(selection_json)

    all_curating_points = []
    total_points_for_curating = 0
    context = {
        'photos': {}
    }

    if selection and len(selection) > 0 and profile is not None and curator_album_selection_form.is_valid():
        general_albums = Album.objects.filter(id__in=request.POST.getlist('albums'))
        if general_albums.exists():
            context['album_id'] = general_albums[0].pk
        else:
            context['album_id'] = None
        default_album = Album(
            name=f'{str(profile.id)}-{str(timezone.now())}',
            atype=Album.AUTO,
            profile=profile,
            is_public=False,
        )
        default_album.save()
        # 15 => unknown copyright
        unknown_licence = Licence.objects.get(pk=15)
        flickr_licence = Licence.objects.filter(url='https://www.flickr.com/commons/usage/').first()
        for k, v in selection.items():
            upload_form = CuratorPhotoUploadForm(v)
            created_album_photo_links = []
            awarded_curator_points = []
            if upload_form.is_valid():
                if upload_form.cleaned_data['institution']:
                    if upload_form.cleaned_data['institution'] == 'Flickr Commons':
                        licence = flickr_licence
                    else:
                        # For Finna
                        if upload_form.cleaned_data['licence']:
                            licence = Licence.objects.filter(name=upload_form.cleaned_data['licence']).first()
                            if not licence:
                                licence = Licence.objects.filter(url=upload_form.cleaned_data['licenceUrl']).first()
                            if not licence:
                                licence_name = upload_form.cleaned_data['licence']
                                if upload_form.cleaned_data['licence'] == upload_form.cleaned_data['licenceUrl']:
                                    licence_name = _get_licence_name_by_url(upload_form.cleaned_data['licenceUrl'])

                                licence = Licence(
                                    name=licence_name,
                                    url=upload_form.cleaned_data['licenceUrl'] or ''
                                )
                                licence.save()
                        else:
                            licence = unknown_licence
                    upload_form.cleaned_data['institution'] = upload_form.cleaned_data['institution'].split(',')[0]
                    if upload_form.cleaned_data['institution'] == 'ETERA':
                        upload_form.cleaned_data['institution'] = 'TLÜAR ETERA'
                    try:
                        source = Source.objects.get(description=upload_form.cleaned_data['institution'])
                    except ObjectDoesNotExist:
                        source = Source(
                            name=upload_form.cleaned_data['institution'],
                            description=upload_form.cleaned_data['institution']
                        )
                        source.save()
                else:
                    licence = unknown_licence
                    source = Source.objects.get(name='AJP')
                existing_photo = None
                if upload_form.cleaned_data['id'] and upload_form.cleaned_data['id'] != '':
                    if upload_form.cleaned_data['collections'] == 'DIGAR':
                        incoming_muis_id = upload_form.cleaned_data['identifyingNumber']
                    else:
                        incoming_muis_id = upload_form.cleaned_data['id']
                    if 'ETERA' in upload_form.cleaned_data['institution']:
                        upload_form.cleaned_data['types'] = 'photo'
                    if '_' in incoming_muis_id \
                            and not ('finna.fi' in upload_form.cleaned_data['urlToRecord']) \
                            and not ('europeana.eu' in upload_form.cleaned_data['urlToRecord']):
                        muis_id = incoming_muis_id.split('_')[0]
                        muis_media_id = incoming_muis_id.split('_')[1]
                    else:
                        muis_id = incoming_muis_id
                        muis_media_id = None
                    if upload_form.cleaned_data['collections'] == 'DIGAR':
                        upload_form.cleaned_data['identifyingNumber'] = \
                            f'nlib-digar:{upload_form.cleaned_data["identifyingNumber"]}'
                        muis_media_id = 1
                    try:
                        if muis_media_id:
                            existing_photo = Photo.objects.filter(
                                source=source, external_id=muis_id, external_sub_id=muis_media_id).get()
                        else:
                            existing_photo = Photo.objects.filter(
                                source=source, external_id=muis_id).get()
                    except ObjectDoesNotExist:
                        pass
                    if not existing_photo:
                        new_photo = None
                        if upload_form.cleaned_data['date'] == '[]':
                            upload_form.cleaned_data['date'] = None
                        try:
                            new_photo = Photo(
                                user=profile,
                                author=upload_form.cleaned_data['creators'],
                                description=upload_form.cleaned_data['title'].rstrip(),
                                source=source,
                                types=upload_form.cleaned_data['types'] if upload_form.cleaned_data['types'] else None,
                                keywords=upload_form.cleaned_data['keywords'].strip() if upload_form.cleaned_data[
                                    'keywords'] else None,
                                date_text=upload_form.cleaned_data['date'] if upload_form.cleaned_data[
                                    'date'] else None,
                                licence=licence,
                                external_id=muis_id,
                                external_sub_id=muis_media_id,
                                source_key=upload_form.cleaned_data['identifyingNumber'],
                                source_url=upload_form.cleaned_data['urlToRecord'],
                                flip=upload_form.cleaned_data['flip'],
                                invert=upload_form.cleaned_data['invert'],
                                stereo=upload_form.cleaned_data['stereo'],
                                rotated=upload_form.cleaned_data['rotated']
                            )
                            new_photo.save()
                            if upload_form.cleaned_data['collections'] == 'DIGAR':
                                new_photo.image = f'uploads/DIGAR_{str(new_photo.source_key).split(":")[1]}_1.jpg'
                            else:
                                # Enable plain http and broken SSL
                                ssl._create_default_https_context = ssl._create_unverified_context
                                opener = build_opener()
                                headers = [('User-Agent', settings.UA)]
                                if etera_token:
                                    headers.append(('Authorization', f'Bearer {etera_token}'))
                                opener.addheaders = headers
                                img_response = opener.open(upload_form.cleaned_data['imageUrl'])
                                if 'ETERA' in new_photo.source.description:
                                    img = ContentFile(img_response.read())
                                    new_photo.image_no_watermark.save('etera.jpg', img)
                                    new_photo.watermark()
                                else:
                                    new_photo.image.save('muis.jpg', ContentFile(img_response.read()))
                            if new_photo.invert:
                                photo_path = f'{settings.MEDIA_ROOT}/{str(new_photo.image)}'
                                img = Image.open(photo_path)
                                inverted_grayscale_image = ImageOps.invert(img).convert('L')
                                inverted_grayscale_image.save(photo_path)
                            if new_photo.rotated is not None and new_photo.rotated > 0:
                                photo_path = f'{settings.MEDIA_ROOT}/{str(new_photo.image)}'
                                img = Image.open(photo_path)
                                rot = img.rotate(new_photo.rotated, expand=1)
                                rot.save(photo_path)
                                new_photo.width, new_photo.height = rot.size
                            if new_photo.flip:
                                photo_path = f'{settings.MEDIA_ROOT}/{str(new_photo.image)}'
                                img = Image.open(photo_path)
                                flipped_image = img.transpose(Image.FLIP_LEFT_RIGHT)
                                flipped_image.save(photo_path)
                            context['photos'][k] = {}
                            context['photos'][k]['message'] = _('OK')

                            lat=None
                            lng=None
                            try:
                                if 'latitude' in upload_form.cleaned_data \
                                   and upload_form.cleaned_data['latitude'] !=None \
                                   and upload_form.cleaned_data['latitude']>0 \
                                   and 'longitude' in upload_form.cleaned_data \
                                   and upload_form.cleaned_data['longitude'] !=None \
                                   and upload_form.cleaned_data['longitude']>0: 
                                        lat = upload_form.cleaned_data['latitude']
                                        lng = upload_form.cleaned_data['longitude']
                            except:
                                print("lat,lng conversion failed")

                            gt_exists = GeoTag.objects.filter(type=GeoTag.SOURCE_GEOTAG,
                                                              photo__source_key=new_photo.source_key).exists()
                            if lat and lng and not gt_exists:
                                source_geotag = GeoTag(
                                    lat=lat,
                                    lon=lng,
                                    origin=GeoTag.SOURCE,
                                    type=GeoTag.SOURCE_GEOTAG,
                                    map_type=GeoTag.NO_MAP,
                                    photo=new_photo,
                                    is_correct=True,
                                    trustworthiness=0.07
                                )
                                source_geotag.save()
                                new_photo.latest_geotag = source_geotag.created
                                new_photo.set_calculated_fields()
                            new_photo.image
                            new_photo.save()
                            new_photo.set_aspect_ratio()
                            new_photo.add_to_source_album()
                            new_photo.find_similar()
                            points_for_curating = Points(action=Points.PHOTO_CURATION, photo=new_photo, points=50,
                                                         user=profile, created=new_photo.created,
                                                         album=general_albums[0])
                            points_for_curating.save()
                            awarded_curator_points.append(points_for_curating)
                            if general_albums.exists():
                                for a in general_albums:
                                    ap = AlbumPhoto(photo=new_photo, album=a, profile=profile, type=AlbumPhoto.CURATED)
                                    ap.save()
                                    created_album_photo_links.append(ap)
                                    if not a.cover_photo:
                                        a.cover_photo = new_photo
                                        a.light_save()
                                for b in general_albums[1:]:
                                    points_for_curating = Points(action=Points.PHOTO_RECURATION, photo=new_photo,
                                                                 points=30,
                                                                 user=profile, created=new_photo.created,
                                                                 album=b)
                                    points_for_curating.save()
                                    awarded_curator_points.append(points_for_curating)
                                    all_curating_points.append(points_for_curating)
                            ap = AlbumPhoto(photo=new_photo, album=default_album, profile=profile,
                                            type=AlbumPhoto.CURATED)
                            ap.save()
                            created_album_photo_links.append(ap)
                            context['photos'][k]['success'] = True
                            all_curating_points.append(points_for_curating)
                        except Exception as e:
                            if new_photo:
                                new_photo.image.delete()
                                new_photo.delete()
                            for ap in created_album_photo_links:
                                ap.delete()
                            for cp in awarded_curator_points:
                                cp.delete()
                            context['photos'][k] = {}
                            context['photos'][k]['error'] = _('Error uploading file: %s (%s)' %
                                                              (e, upload_form.cleaned_data['imageUrl']))
                    else:
                        if general_albums.exists():
                            for a in general_albums:
                                ap = AlbumPhoto(photo=existing_photo, album=a, profile=profile,
                                                type=AlbumPhoto.RECURATED)
                                ap.save()
                                points_for_recurating = Points(user=profile, action=Points.PHOTO_RECURATION,
                                                               photo=existing_photo, points=30,
                                                               album=general_albums[0], created=timezone.now())
                                points_for_recurating.save()
                                all_curating_points.append(points_for_recurating)
                        dap = AlbumPhoto(photo=existing_photo, album=default_album, profile=profile,
                                         type=AlbumPhoto.RECURATED)
                        dap.save()
                        context['photos'][k] = {}
                        context['photos'][k]['success'] = True
                        context['photos'][k]['message'] = _('Photo already exists in Ajapaik')
            else:
                context['photos'][k] = {}
                context['photos'][k]['error'] = _('Error uploading file: %s (%s)'
                                                  % (upload_form.errors, upload_form.cleaned_data['imageUrl']))

        if general_albums:
            game_reverse = request.build_absolute_uri(reverse('game'))
            for ga in general_albums:
                requests.post(
                    f'https://graph.facebook.com/v7.0/?id={game_reverse}?album={str(ga.id)}&scrape=true'
                )
        for cp in all_curating_points:
            total_points_for_curating += cp.points
        context['total_points_for_curating'] = total_points_for_curating
        if general_albums.exists():
            for album in general_albums:
                album.save()
                if album.subalbum_of:
                    album.subalbum_of.save()
    else:
        if not selection or len(selection) == 0:
            error = _('Please add pictures to your album')
        else:
            error = _('Not enough data submitted')
        context = {
            'error': error
        }
    return HttpResponse(json.dumps(context), content_type='application/json')


def update_like_state(request):
    profile = request.get_user().profile
    form = PhotoLikeForm(request.POST)
    context = {}
    if form.is_valid() and profile:
        p = form.cleaned_data['photo']
        like = PhotoLike.objects.filter(photo=p, profile=profile).first()
        if like:
            if like.level == 1:
                like.level = 2
                like.save()
                context['level'] = 2
            elif like.level == 2:
                like.delete()
                context['level'] = 0
                p.first_like = None
                p.latest_list = None
        else:
            like = PhotoLike(
                profile=profile,
                photo=p,
                level=1
            )
            like.save()
            context['level'] = 1
        like_sum = p.likes.aggregate(Sum('level'))['level__sum']
        if not like_sum:
            like_sum = 0
        like_count = p.likes.distinct('profile').count()
        context['likeCount'] = like_count
        p.like_count = like_sum
        if like_count > 0:
            first_like = p.likes.order_by('created').first()
            latest_like = p.likes.order_by('-created').first()
            if first_like:
                p.first_like = first_like.created
            if latest_like:
                p.latest_like = latest_like.created
        else:
            p.first_like = None
            p.latest_like = None
        p.light_save()

    return HttpResponse(json.dumps(context), content_type='application/json')


def muis_import(request):
    user = request.user
    user_can_import = not user.is_anonymous and \
                      user.profile.is_legit and user.groups.filter(name='csv_uploaders').exists()
    if request.method == 'GET':
        url = 'https://www.muis.ee/OAIService/OAIService?verb=ListSets'
        url_response = urllib.request.urlopen(url)
        parser = ET.XMLParser(encoding="utf-8")
        tree = ET.fromstring(url_response.read(), parser=parser)
        ns = {'d': 'http://www.openarchives.org/OAI/2.0/'}
        sets = tree.findall('d:ListSets/d:set', ns)
        for s in sets:
            name = s.find('d:setName', ns).text
            spec = s.find('d:setSpec', ns).text
            existing = MuisCollection.objects.filter(spec=spec).first()
            if existing is None:
                MuisCollection(name=name, spec=spec).save()
            elif existing.name != name:
                existing.name = name
                existing.save()
        collections = MuisCollection.objects.filter(blacklisted=False)
        return render(request, 'muis-import.html', {
            'user_can_import': user_can_import,
            'collections': collections
        })


@user_passes_test(lambda u: u.groups.filter(name='csv_uploaders').exists(), login_url='/admin/')
def csv_import(request):
    if request.method == 'GET':
        form = CsvImportForm
        return render(request, 'csv/csv-import.html', {'form': form})

    if request.method == 'POST':
        csv_file = request.FILES['csv_file']
        decoded_file = csv_file.read().decode('utf-8').splitlines()
        existing_file_list = []
        errors = []
        file_list = []
        missing_album_list = []
        missing_licence_list = []
        not_found_list = []
        profile = request.get_user().profile
        skipped_list = []
        success = None
        unique_album_list = []
        upload_folder = f'{settings.MEDIA_ROOT}/uploads/'
        final_image_folder = 'uploads/'

        if 'zip_file' in request.FILES:
            file_obj = request.FILES['zip_file']
            import_folder = f'{settings.MEDIA_ROOT}/import'
            zip_filename = f'{settings.MEDIA_ROOT}/import{str(uuid4())}.zip'

            with default_storage.open(zip_filename, 'wb+') as destination:
                for chunk in file_obj.chunks():
                    destination.write(chunk)

            with ZipFile(zip_filename, 'r') as zip_ref:
                zip_ref.extractall(import_folder)

            file_names = os.listdir(import_folder)
            for name in file_names:
                if '.' in name:
                    os.chmod(f'{import_folder}/{name}', 0o0664)
                    if not os.path.exists(f'{upload_folder}/{name}'):
                        shutil.move(os.path.join(import_folder, name), upload_folder)
                    else:
                        existing_file_list.append(upload_folder + name)
                        os.remove(f'{import_folder}/{name}')
                else:
                    def del_evenReadonly(action, name, exc):
                        os.chmod(name, stat.S_IWRITE)
                        os.remove(name)

                    shutil.rmtree(f'{import_folder}/{name}', onerror=del_evenReadonly)
            os.remove(zip_filename)
            os.rmdir(import_folder)

        for row in csv.DictReader(decoded_file, delimiter=',', quotechar='"'):
            file_list.append(final_image_folder + row['file'])

        existing_photos = Photo.objects.filter(image__in=file_list).values_list('image', flat=True)

        # TODO: map over row fields instead to directly set attributes of photo with setattr
        # before doing so remove any exceptions like album, source, licence or start using only ids
        for row in csv.DictReader(decoded_file, delimiter=',', quotechar='"'):
            if existing_photos.exists() and f"{upload_folder}{row['file']}" in list(existing_photos):
                skipped_list.append(row['file'])
                continue
            album_ids = row['album'].replace(' ', '').split(',')
            person_album_ids = row['person_album'].replace(' ', '').split(',')
            author = None
            keywords = None
            geography = None
            lat = None
            lon = None
            licence = None
            source = None
            source_url = None
            source_key = None
            date_text = None
            description = None
            description_et = None
            description_en = None
            description_fi = None
            description_ru = None
            title = None
            title_et = None
            title_en = None
            title_fi = None
            title_ru = None
            types = None
            if 'author' in row.keys():
                author = row['author']
            if 'keywords' in row.keys():
                keywords = row['keywords']
            if 'lat' in row.keys() and row['lat'] != '':
                lat = row['lat']
            if 'lon' in row.keys() and row['lon'] != '':
                lon = row['lon']
            if lat and lon:
                geography = Point(x=float(lon), y=float(lat), srid=4326)
            if 'licence' in row.keys():
                licence = Licence.objects.filter(id=row['licence']).first()
                if licence is None and not row['licence'] in missing_licence_list:
                    missing_licence_list.append(row['licence'])
            if 'source' in row.keys():
                source = Source.objects.filter(id=row['source']).first()
            if 'source_key' in row.keys():
                source_key = row['source_key']
            if 'source_url' in row.keys():
                source_url = row['source_url']
            if 'date_text' in row.keys():
                date_text = row['date_text']
            if 'description' in row.keys():
                description = row['description']
            if 'description_et' in row.keys():
                description_et = row['description_et']
            if 'description_en' in row.keys():
                description_en = row['description_en']
            if 'description_fi' in row.keys():
                description_fi = row['description_fi']
            if 'description_ru' in row.keys():
                description_ru = row['description_ru']
            if 'title' in row.keys():
                title = row['title']
            if 'title_et' in row.keys():
                title_et = row['title_et']
            if 'title_en' in row.keys():
                title_en = row['title_en']
            if 'title_fi' in row.keys():
                title_fi = row['title_fi']
            if 'title_ru' in row.keys():
                title_ru = row['title_ru']
            if 'types' in row.keys():
                types = row['types']

            try:
                photo = Photo(
                    image=upload_folder + row['file'],
                    author=author,
                    keywords=keywords,
                    lat=lat,
                    lon=lon,
                    geography=geography,
                    source=source,
                    source_key=source_key,
                    source_url=source_url,
                    date_text=date_text,
                    licence=licence,
                    user=profile,
                    description=description,
                    title=title,
                    types=types
                )
                photo.save()
                photo = Photo.objects.get(id=photo.id)
                photo.image.name = final_image_folder + row['file']
                if description_et:
                    photo.description_et = description_et
                if description_en:
                    photo.description_en = description_en
                if description_fi:
                    photo.description_fi = description_fi
                if description_ru:
                    photo.description_ru = description_ru
                if title_et:
                    photo.title_et = title_et
                if title_en:
                    photo.title_en = title_en
                if title_fi:
                    photo.title_fi = title_fi
                if title_ru:
                    photo.title_ru = title_ru
                photo.light_save()

                if geography:
                    geotag = GeoTag(
                        lat=lat,
                        lon=lon,
                        origin=GeoTag.SOURCE,
                        type=GeoTag.SOURCE_GEOTAG,
                        map_type=GeoTag.NO_MAP,
                        photo=photo,
                        is_correct=True,
                        trustworthiness=0.07,
                        geography=geography,
                    )
                    geotag.save()
            except FileNotFoundError as not_found:
                not_found_list.append(not_found.filename.replace(upload_folder, ''))
                continue

            for album_id in album_ids:
                try:
                    album_id = int(album_id)
                except Exception as e:
                    print(e)
                    continue
                album = Album.objects.filter(id=album_id).first()
                if album is None:
                    missing_album_list.append(album_id)
                else:
                    if album_id not in unique_album_list:
                        unique_album_list.append(album_id)
                    ap = AlbumPhoto(photo=photo, album=album, type=AlbumPhoto.CURATED)
                    ap.save()

                    action = Points.PHOTO_CURATION
                    points = 50
                    points_for_curating = Points(
                        action=action,
                        photo=photo,
                        points=points,
                        user=profile,
                        created=photo.created,
                        album=album
                    )
                    points_for_curating.save()

                    if not album.cover_photo:
                        album.cover_photo = photo
                        album.light_save()

            for person_album_id in person_album_ids:
                try:
                    person_album_id = int(person_album_id)
                except Exception as e:
                    print(e)
                    continue
                album = Album.objects.filter(id=person_album_id).first()
                if album is None:
                    missing_album_list.append(person_album_id)
                else:
                    if person_album_id not in unique_album_list:
                        unique_album_list.append(person_album_id)
                    ap = AlbumPhoto(photo=photo, album=album, type=AlbumPhoto.FACE_TAGGED)
                    ap.save()

                    if not album.cover_photo:
                        album.cover_photo = photo
                        album.light_save()
        all_albums = Album.objects.filter(id__in=unique_album_list)
        if all_albums.exists():
            for album in all_albums:
                album.set_calculated_fields()
                album.save()
        if len(existing_file_list) > 0:
            existing_file_error = 'The following images already existed on the server, they were not replaced:'
            errors.append({'message': existing_file_error, 'list': list(set(existing_file_list))})
        if len(missing_licence_list) > 0:
            missing_licence_error = 'The following licences do not exist:'
            errors.append({'message': missing_licence_error, 'list': list(set(missing_licence_list))})
        if len(missing_album_list) > 0:
            missing_albums_error = "The albums with following IDs do not exist:"
            errors.append({'message': missing_albums_error, 'list': list(set(missing_album_list))})
        if len(not_found_list) > 0:
            missing_files_error = "Some files are missing from disk, thus they were not added:"
            errors.append({'message': missing_files_error, 'list': list(set(not_found_list))})
        if len(skipped_list) > 0:
            already_exists_error = "Some imports were skipped since they already exist on Ajapaik:"
            errors.append({'message': already_exists_error, 'list': list(set(skipped_list))})
        if len(errors) < 1:
            success = 'OK'

        form = CsvImportForm
        return render(request, 'csv/csv-import.html', {'form': form, 'errors': errors, 'success': success})


def submit_dating(request):
    profile = request.get_user().profile
    form = DatingSubmitForm(request.POST.copy())
    confirm_form = DatingConfirmForm(request.POST)
    form.data['profile'] = profile.id
    if form.is_valid():
        dating = form.save(commit=False)
        if not dating.start:
            dating.start = datetime.datetime.strptime('01011000', '%d%m%Y').date()
        if not dating.end:
            dating.end = datetime.datetime.strptime('01013000', '%d%m%Y').date()
        p = form.cleaned_data['photo']
        dating_exists = Dating.objects.filter(profile=profile, raw=dating.raw, photo=p).exists()
        if not dating_exists:
            dating.save()
            p.latest_dating = dating.created
            if not p.first_dating:
                p.first_dating = dating.created
            confirmation_count = 0
            for each in p.datings.all():
                confirmation_count += each.confirmations.count()
            p.dating_count = p.datings.count() + confirmation_count
            p.light_save()
            Points(
                user=profile,
                action=Points.DATING,
                photo=form.cleaned_data['photo'],
                dating=dating,
                points=settings.DATING_POINTS,
                created=dating.created
            ).save()
            return HttpResponse('OK')
        return HttpResponse('Dating exists', status=400)
    elif confirm_form.is_valid():
        original_dating = confirm_form.cleaned_data['id']
        confirmation_exists = DatingConfirmation.objects.filter(confirmation_of=original_dating,
                                                                profile=profile).exists()
        if not confirmation_exists and original_dating.profile != profile:
            new_confirmation = DatingConfirmation(
                confirmation_of=original_dating,
                profile=profile
            )
            new_confirmation.save()
            p = original_dating.photo
            p.latest_dating = new_confirmation.created
            confirmation_count = 0
            for each in p.datings.all():
                confirmation_count += each.confirmations.count()
            p.dating_count = p.datings.count() + confirmation_count
            p.light_save()
            Points(
                user=profile,
                action=Points.DATING_CONFIRMATION,
                dating_confirmation=new_confirmation,
                points=settings.DATING_CONFIRMATION_POINTS,
                photo=p,
                created=new_confirmation.created
            ).save()
            return HttpResponse('OK')
        else:
            return HttpResponse('Already confirmed or confirming your own dating', status=400)
    else:
        return HttpResponse('Invalid data', status=400)


def get_datings(request, photo_id):
    photo = Photo.objects.filter(pk=photo_id).first()
    profile = request.get_user().profile
    context = {}
    if photo:
        datings = photo.datings.order_by('created').prefetch_related('confirmations')
        for each in datings:
            each.this_user_has_confirmed = each.confirmations.filter(profile=profile).exists()
        datings_serialized = DatingSerializer(datings, many=True).data
        context['datings'] = datings_serialized

    return HttpResponse(json.dumps(context), content_type='application/json')


def generate_still_from_video(request):
    profile = request.get_user().profile
    form = VideoStillCaptureForm(request.POST)
    context = {}
    if form.is_valid():
        a = form.cleaned_data['album']
        vid = form.cleaned_data['video']
        time = form.cleaned_data['timestamp']
        still = Photo.objects.filter(video=vid, video_timestamp=time).first()
        if not still:
            vidcap = cv2.VideoCapture(vid.file.path)
            vidcap.set(0, time)
            success, image = vidcap.read()
            source = Source.objects.filter(name='AJP').first()
            if success:
                tmp = NamedTemporaryFile(suffix='.jpeg', delete=True)
                cv2.imwrite(tmp.name, image)
                hours, milliseconds = divmod(time, 3600000)
                minutes, milliseconds = divmod(time, 60000)
                seconds = float(milliseconds) / 1000
                s = "%i:%02i:%06.3f" % (hours, minutes, seconds)
                description = _('Still from "%(film)s" at %(time)s') % {'film': vid.name, 'time': s}
                still = Photo(
                    description=description,
                    user=profile,
                    types='film,still,frame,snapshot,filmi,kaader,pilt',
                    video=vid,
                    video_timestamp=time,
                    source=source
                )
                still.save()
                still.source_key = still.id
                still.source_url = request.build_absolute_uri(
                    reverse('photo', args=(still.id, still.get_pseudo_slug())))
                still.image.save(
                    f'{unicodedata.normalize("NFKD", description)}.jpeg',
                    File(tmp))
                still.light_save()
                AlbumPhoto(album=a, photo=still, profile=profile, type=AlbumPhoto.STILL).save()
                Points(
                    user=profile,
                    action=Points.FILM_STILL,
                    photo=still,
                    album=a,
                    points=50,
                    created=still.created
                ).save()
                a.set_calculated_fields()
                a.save()
                still.add_to_source_album()
        context['stillId'] = still.id

    return HttpResponse(json.dumps(context), content_type='application/json')


def donate(request):
    pictures = [
        {
            'image_url': 'https://ajapaik.ee//media/uploads/2016/08/07/muis_eU4vJ5H.jpg',
            'resource_url': 'https://ajapaik.ee/photo/82938/'
        },
        {
            'image_url': 'https://ajapaik.ee//media/uploads/2017/03/27/muis_5dtkncr.jpg',
            'resource_url': 'https://ajapaik.ee/photo/111092'
        },
        {
            'image_url': 'https://ajapaik.ee/media/uploads/2016/10/10/muis_YHWUAey.jpg',
            'resource_url': 'https://ajapaik.ee/photo/89376'
        }
    ]
    context = {
        'is_donate': True,
        'picture': choice(pictures)
    }

    return render(request, 'donate/donate.html', context)


def photo_upload_choice(request):
    user = request.user
    context = {
        'is_upload_choice': True,
        'ajapaik_facebook_link': settings.AJAPAIK_FACEBOOK_LINK,
        'user_can_import_from_csv': user.is_superuser and user.groups.filter(name='csv_uploaders').exists(),
        'user_can_import_from_muis': user.is_superuser and user.groups.filter(name='csv_uploaders').exists()
    }

    return render(request, 'photo/upload/photo_upload_choice.html', context)


def upload_photo_to_wikimedia_commons(request, path):
    social_token = None
    if request.user and request.user.profile:
        social_account = SocialAccount.objects.filter(user=request.user).first()
        social_token = SocialToken.objects.filter(account=social_account, expires_at__gt=datetime.date.today()).last()
    if social_token:
        S = requests.Session()
        URL = "https://commons.wikimedia.org/w/api.php"
        FILE_PATH = path

        # Step 1: Retrieve a login token
        PARAMS_1 = {
            "action": "query",
            "meta": "tokens",
            "type": "login",
            "format": "json"
        }

        headers = {
            "Authentication": "Bearer " + social_token.token
        }

        R = S.get(url=URL, params=PARAMS_1, headers=headers)
        DATA = R.json()
        print(DATA)

        LOGIN_TOKEN = DATA["query"]["tokens"]["logintoken"]

        # Step 2: Send a post request to login. Use of main account for login is not
        # supported. Obtain credentials via Special:BotPasswords
        # (https://www.mediawiki.org/wiki/Special:BotPasswords) for lgname & lgpassword
        PARAMS_2 = {
            "action": "login",
            "format": "json",
            "lgtoken": LOGIN_TOKEN
        }

        R = S.post(URL, data=PARAMS_2, headers=headers)
        DATA = R.json()
        print(DATA)

        # Step 3: Obtain a CSRF token
        PARAMS_3 = {
            "action": "query",
            "meta": "tokens",
            "format": "json"
        }

        R = S.get(url=URL, params=PARAMS_3, headers=headers)

        DATA = R.json()
        print(DATA)

        CSRF_TOKEN = DATA["query"]["tokens"]["csrftoken"]

        # Step 4: Post request to upload a file directly
        PARAMS_4 = {
            "action": "upload",
            "filename": "file_1.jpg",
            "format": "json",
            "token": CSRF_TOKEN,
            "ignorewarnings": 1
        }

        FILE = {'file': ('file_1.jpg', open(FILE_PATH, 'rb'), 'multipart/form-data')}

        R = S.post(URL, files=FILE, data=PARAMS_4)
        DATA = R.json()
        print(DATA)


def rephoto_upload_settings_modal(request):
    form = None
    if (hasattr(request.user, 'profile')):
        profile = request.user.profile
        form = RephotoUploadSettingsForm(
            data={'wikimedia_commons_rephoto_upload_consent': profile.wikimedia_commons_rephoto_upload_consent})

    context = {
        'form': form,
        'isModal': True
    }

    return render(request, 'rephoto_upload/_rephoto_upload_settings_modal_content.html', context)


def compare_all_photos(request, photo_id=None, photo_id_2=None):
    return compare_photos_generic(request, photo_id, photo_id_2, 'compare-all-photos', True)


def compare_photos(request, photo_id=None, photo_id_2=None):
    return compare_photos_generic(request, photo_id, photo_id_2)


def compare_photos_generic(request, photo_id=None, photo_id_2=None, view='compare-photos', compare_all=False):
    profile = request.get_user().profile
    similar_photos = None
    if photo_id is None or photo_id_2 is None:
        first_similar = ImageSimilarity.objects.filter(confirmed=False).first()
        if first_similar is None:
            suggestions = ImageSimilaritySuggestion.objects.filter(proposer_id=profile.id) \
                .order_by('proposer_id', '-created').all().values_list('image_similarity_id', flat=True)
            if suggestions is None:
                similar_photos = ImageSimilarity.objects.all()
            else:
                similar_photos = ImageSimilarity.objects.exclude(id__in=suggestions)
            if similar_photos is None or len(similar_photos) < 1:
                return render(request, 'compare_photos/compare_photos_no_results.html')
            first_similar = similar_photos.first()
        photo_id = first_similar.from_photo_id
        photo_id_2 = first_similar.to_photo_id
    photo_obj = get_object_or_404(Photo, id=photo_id)
    photo_obj2 = get_object_or_404(Photo, id=photo_id_2)
    first_photo_criterion = Q(from_photo=photo_obj) & Q(to_photo=photo_obj2)
    second_photo_criterion = Q(from_photo=photo_obj2) & Q(to_photo=photo_obj)
    master_criterion = Q(first_photo_criterion | second_photo_criterion)
    if similar_photos is None or len(similar_photos) < 1:
        similar_photos = ImageSimilarity.objects.exclude(master_criterion | Q(confirmed=True))
        first_photo = similar_photos.filter(Q(from_photo=photo_obj) & Q(confirmed=False)).first()
        second_photo = similar_photos.filter(Q(from_photo=photo_obj2) & Q(confirmed=False)).first()
    else:
        first_photo = similar_photos.filter(from_photo=photo_obj).first()
        second_photo = similar_photos.filter(from_photo=photo_obj2).first()
    if first_photo is not None:
        next_pair = first_photo
    elif (second_photo is not None):
        next_pair = second_photo
    else:
        if compare_all is True:
            next_pair = similar_photos.first()
        else:
            next_pair = None
    if next_pair is None:
        next_action = request.build_absolute_uri(reverse('photo', args=(photo_obj.id, photo_obj.get_pseudo_slug())))
    else:
        next_action = request.build_absolute_uri(reverse(view, args=(next_pair.from_photo.id, next_pair.to_photo.id)))

    context = {
        'is_comparephoto': True,
        'ajapaik_facebook_link': settings.AJAPAIK_FACEBOOK_LINK,
        'photo': photo_obj,
        'photo2': photo_obj2,
        'next_action': next_action
    }
    return render(request, 'compare_photos/compare_photos.html', context)


def user_upload(request):
    context = {
        'ajapaik_facebook_link': settings.AJAPAIK_FACEBOOK_LINK,
        'is_user_upload': True,
        'show_albums_error': False
    }
    if request.method == 'POST':
        form = UserPhotoUploadForm(request.POST, request.FILES)
        albums = request.POST.getlist('albums')
        if form.is_valid() and albums is not None and len(albums) > 0:
            photo = form.save(commit=False)
            photo.user = request.user.profile
            if photo.uploader_is_author:
                photo.author = request.user.profile.get_display_name
                photo.licence = Licence.objects.get(id=17)  # CC BY 4.0
            photo.save()
            photo.set_aspect_ratio()
            photo.find_similar()
            albums = request.POST.getlist('albums')
            album_photos = []
            for each in albums:
                album_photos.append(
                    AlbumPhoto(photo=photo,
                               album=Album.objects.filter(id=each).first(),
                               type=AlbumPhoto.UPLOADED,
                               profile=request.user.profile
                               ))
            AlbumPhoto.objects.bulk_create(album_photos)
            for a in albums:
                album = Album.objects.filter(id=a).first()
                if album is not None:
                    album.set_calculated_fields()
                    album.light_save()
            form = UserPhotoUploadForm()
            photo.add_to_source_album()
            if request.POST.get('geotag') == 'true':
                return redirect(f'{reverse("frontpage_photos")}?photo={str(photo.id)}&locationToolsOpen=1')
            else:
                context['message'] = _('Photo uploaded')
        if albums is None or len(albums) < 1:
            context['show_albums_error'] = True
    else:
        form = UserPhotoUploadForm()
    context['form'] = form

    return render(request, 'user_upload/user_upload.html', context)


def user_upload_add_album(request):
    context = {
        'ajapaik_facebook_link': settings.AJAPAIK_FACEBOOK_LINK
    }
    if request.method == 'POST':
        form = UserPhotoUploadAddAlbumForm(request.POST, profile=request.user.profile)
        if form.is_valid():
            album = form.save(commit=False)
            album.profile = request.user.profile
            album.save()
            context['message'] = _('Album created')
    else:
        form = UserPhotoUploadAddAlbumForm(profile=request.user.profile)
    context['form'] = form

    return render(request, 'user_upload/user_upload_add_album.html', context)


def get_comment_like_count(request, comment_id):
    comment = get_object_or_404(
        django_comments.get_model(), pk=comment_id, site__pk=settings.SITE_ID
    )

    return JsonResponse({
        'like_count': comment.like_count(),
        'dislike_count': comment.dislike_count()
    })


class CommentList(View):
    '''Render comment list. Only comment that not marked as deleted should be shown.'''
    template_name = 'comments/list.html'
    comment_model = django_comments.get_model()
    form_class = django_comments.get_form()

    def _aggregate_comment_and_replies(self, comments, flat_comment_list):
        '''Recursively build comments and their replies list.'''
        for comment in comments:
            flat_comment_list.append(comment)
            subcomments = get_comment_replies(comment).filter(
                is_removed=False
            ).order_by('submit_date')
            self._aggregate_comment_and_replies(
                comments=subcomments, flat_comment_list=flat_comment_list
            )

    def get(self, request, photo_id):
        flat_comment_list = []
        # Selecting photo's top level comments(pk == parent_id) and that has
        # been not removed.
        comments = self.comment_model.objects.filter(
            object_pk=photo_id, parent_id=F('pk'), is_removed=False
        ).order_by('submit_date')
        self._aggregate_comment_and_replies(
            comments=comments, flat_comment_list=flat_comment_list
        )
        content = render_to_string(
            template_name=self.template_name,
            request=request,
            context={
                'comment_list': flat_comment_list,
                'reply_form': self.form_class(get_object_or_404(
                    Photo, pk=photo_id)),
            }
        )
        comments_count = len(flat_comment_list)
        return JsonResponse({
            'content': content,
            'comments_count': comments_count,
        })


class PostComment(View):
    form_class = django_comments.get_form()

    def post(self, request, photo_id):
        form = self.form_class(
            target_object=get_object_or_404(Photo, pk=photo_id),
            data=request.POST
        )
        if form.is_valid():
            response = post_comment(request)
            if response.status_code != 302:
                return JsonResponse({
                    'comment': [_('Sorry but we fail to post your comment.')]
                })
        return JsonResponse(form.errors)


class EditComment(View):
    form_class = django_comments.get_form()

    def post(self, request):
        form = EditCommentForm(request.POST)
        if form.is_valid() and form.comment.user == request.user:
            form.comment.comment = form.cleaned_data['text']
            form.comment.save()
        return JsonResponse(form.errors)


class DeleteComment(View):
    comment_model = django_comments.get_model()

    def _perform_delete(self, request, comment):
        flag, created = CommentFlag.objects.get_or_create(
            comment_id=comment.pk,
            user=request.user,
            flag=CommentFlag.MODERATOR_DELETION
        )
        comment.is_removed = True
        comment.save()
        comment_was_flagged.send(
            sender=comment.__class__,
            comment=comment,
            flag=flag,
            created=created,
            request=request,
        )

    def post(self, request):
        comment_id = request.POST.get('comment_id')
        if comment_id:
            comment = get_object_or_404(self.comment_model, pk=comment_id)
            if comment.user == request.user:
                replies = get_comment_replies(comment)
                self._perform_delete(request, comment)
                for reply in replies:
                    self._perform_delete(request, reply)
        return JsonResponse({
            'status': 200
        })


def privacy(request):
    return render(request, 't&c/privacy.html')


def terms(request):
    return render(request, 't&c/terms.html')


def me(request):
    return redirect('user', user_id=request.get_user().profile.id)

def oauthdone(request):
    user = request.user
    form = OauthDoneForm(request.GET)
    if form.is_valid():
        if user.is_anonymous:
            return HttpResponse('No user found', status=404)

        provider=form.cleaned_data['provider']
        allowed_providers=[ 'facebook', 'google', 'wikimedia-commons']
        if provider not in allowed_providers:
            return HttpResponse('Provider not in allowed providers.' + provider, status=404)

        app = SocialApp.objects.get_current(provider)

        if app == None:
            return HttpResponse('Provider ' + provider + ' not found.', status=404)

        social_token=SocialToken.objects.get(account__user_id=user.id, app=app)
        if social_token == None:
            return HttpResponse('Token not found.', status=404)

        token=social_token.token
        context = {
            'route': '/login',
            'provider': provider,
            'token': token
        }
        return render(request, 'socialaccount/oauthdone.html', context)

    return HttpResponse('No user found', status=404)

def user(request, user_id):
    token = ProfileMergeToken.objects.filter(source_profile_id=user_id, used__isnull=False).order_by('used').first()
    if token is not None and token.target_profile is not None:
        return redirect('user', user_id=token.target_profile.id)
    current_profile = request.get_user().profile
    profile = get_object_or_404(Profile, pk=user_id)
    is_current_user = False
    if current_profile == profile:
        is_current_user = True
    if profile.user.is_anonymous:
        commented_pictures_count = 0
    else:
        commented_pictures_count = MyXtdComment.objects.filter(is_removed=False, user_id=profile.id).order_by('object_pk').distinct('object_pk').count()

    curated_pictures_count            = Photo.objects.filter(user_id=profile.id, rephoto_of__isnull=True).count()
    datings_count                     = Dating.objects.filter(profile_id=profile.id).distinct('photo').count()
    face_annotations_count            = FaceRecognitionRectangle.objects.filter(user_id=profile.id).count()
    face_annotations_pictures_count   = FaceRecognitionRectangle.objects.filter(user_id=profile.id).distinct('photo').count()
    geotags_count                     = GeoTag.objects.filter(user_id=profile.id).exclude(type=GeoTag.CONFIRMATION).distinct('photo').count()
    geotag_confirmations_count        = GeoTag.objects.filter(user_id=profile.id, type=GeoTag.CONFIRMATION).distinct('photo').count()
    object_annotations_count          = ObjectDetectionAnnotation.objects.filter(user_id=profile.id).count()
    object_annotations_pictures_count = ObjectDetectionAnnotation.objects.filter(user_id=profile.id).distinct('photo').count()
    photolikes_count                  = PhotoLike.objects.filter(profile_id=profile.id).distinct('photo').count()
    rephoto_count                     = Photo.objects.filter(user_id=profile.id, rephoto_of__isnull=False).count()
    rephotographed_pictures_count     = Photo.objects.filter(user_id=profile.id, rephoto_of__isnull=False).order_by('rephoto_of_id').distinct('rephoto_of_id').count()
    similar_pictures_count            = ImageSimilaritySuggestion.objects.filter(proposer=profile).distinct('image_similarity').count()
    transcriptions_count              = Transcription.objects.filter(user=profile).distinct('photo').count()

    photo_viewpoint_elevation_suggestions_ids = PhotoViewpointElevationSuggestion.objects.filter(
        proposer_id=profile.id).distinct('photo').values_list('photo_id', flat=True)
    photo_scene_suggestions_count = PhotoSceneSuggestion.objects.filter(proposer_id=profile.id).distinct('photo').exclude(
        photo_id__in=photo_viewpoint_elevation_suggestions_ids).count()

    action_count = commented_pictures_count + transcriptions_count + \
                   object_annotations_count + face_annotations_count + \
                   curated_pictures_count + geotags_count + \
                   rephoto_count + rephoto_count + datings_count + \
                   similar_pictures_count + geotag_confirmations_count + \
                   photolikes_count + photo_scene_suggestions_count + len(photo_viewpoint_elevation_suggestions_ids)

    user_points=profile.points.aggregate(user_points=Sum('points'))['user_points'] 
    if user_points == None:
        user_points = 0

    context = {
        'actions': action_count,
        'commented_pictures': commented_pictures_count,
        'curated_pictures': curated_pictures_count,
        'datings': datings_count,
        'face_annotations': face_annotations_count,
        'face_annotations_pictures': face_annotations_pictures_count,
        'favorites_link': '/?order1=time&order2=added&page=1&myLikes=1',
        'geotag_confirmations': geotag_confirmations_count,
        'geotagged_pictures': geotags_count,
        'is_current_user': is_current_user,
        'object_annotations': object_annotations_count,
        'object_annotations_pictures': object_annotations_pictures_count,
        'photolikes': photolikes_count,
        'photo_suggestions': photo_scene_suggestions_count + len(photo_viewpoint_elevation_suggestions_ids),
        'profile': profile,
        'rephotographed_pictures': rephotographed_pictures_count,
        'rephotos_link': f'/photos/?rephotosBy={str(profile.user_id)}&order1=time&order2=rephotos',
        'rephotos': rephoto_count,
        'similar_pictures': similar_pictures_count,
        'transcriptions': transcriptions_count,
        'user_points': user_points
    }

    return render(request, 'user/user.html', context)


def user_settings_modal(request):
    form = None
    if hasattr(request.user, 'profile'):
        form = UserSettingsForm(data={
            'preferred_language': request.user.profile.preferred_language,
            'newsletter_consent': request.user.profile.newsletter_consent
        })
    context = {
        'form': form,
        'isModal': True
    }

    return render(request, 'user/settings/_user_settings_modal_content.html', context)


def user_settings(request):
    context = {}
    token = request.GET.get('token')
    profile = None
    user_settings_form = None
    invalid = False
    initial = False
    show_accordion = False
    social_account_form = None
    if hasattr(request.user, 'profile'):
        profile = request.user.profile
        context['profile'] = profile
        user_settings_form = UserSettingsForm(data={
            'preferred_language': profile.preferred_language,
            'newsletter_consent': profile.newsletter_consent
        })
    if profile is None:
        return render(request, 'user/settings/user_settings.html', context)

    if token is None:
        if profile and profile.is_legit():
            token = ProfileMergeToken(token=str(uuid4()), profile=profile)
            token.save()
        initial = True
    else:
        token = ProfileMergeToken.objects.filter(token=token, used=None,
                                                 created__gte=datetime.date.today() - datetime.timedelta(
                                                     hours=1)).first()
        if token is None:
            invalid = True
            if profile and profile.is_legit():
                token = ProfileMergeToken(token=str(uuid4()), profile=profile)
                token.save()
            else:
                context['next'] = request.path
        else:
            context['token_profile_social_accounts'] = SocialAccount.objects.filter(user_id=token.profile.user_id)
            context['link'] = reverse('user', args=(token.profile_id,))
    if token and token.token:
        context['next'] = f'{request.path}?token={token.token}'
    context['me'] = reverse('me')
    context['profile_social_accounts'] = SocialAccount.objects.filter(user_id=request.user.id)
    context['token'] = token
    display_name_form = ChangeDisplayNameForm(data={'display_name': profile.display_name, })
    email_form = AddEmailForm()
    context['invalid'] = invalid
    context['initial'] = initial
    if profile:
        show_accordion = not invalid and profile and profile.is_legit and not initial
        context['show_accordion'] = show_accordion
        social_account_form = DisconnectForm(request=request)

    password_accordion = {"id": 4, "heading": "Set password", "template": "account/password_set_form.html",
                          "form": SetPasswordForm(), "show_merge_section": None}
    if request.user.has_usable_password():
        password_accordion = {"id": 4, "heading": _("Change password"), "template": "account/password_change_form.html",
                              "form": ChangePasswordForm(), "show_merge_section": None}

    context['accordions'] = [
        {"id": 1, "heading": _("Change display name"), "template": "user/display_name/change_display_name.html",
         "form": display_name_form, "show_merge_section": None},
        {"id": 2, "heading": _("Newsletter and language settings"),
         "template": "user/settings/_user_settings_modal_content.html", "form": user_settings_form,
         "show_merge_section": None},
        {"id": 3, "heading": _("E-mail addresses"), "template": "account/email_content.html", "form": email_form,
         "show_merge_section": None},
        password_accordion,
        {"id": 5, "heading": _("Account Connections"), "template": "socialaccount/connections_content.html",
         "form": social_account_form, "show_merge_section": None},
        {"id": 6, "heading": _("Merge another Ajapaik account with current one"),
         "template": "user/merge/merge_accounts.html", "form": None, "show_merge_section": show_accordion}
    ]
    return render(request, 'user/settings/user_settings.html', context)


def profile_change_display_name(request):
    form = None
    if (hasattr(request.user, 'profile')):
        form = ChangeDisplayNameForm(data={
            'display_name': request.user.profile.display_name
        })
    context = {
        'form': form
    }

    return render(request, 'user/display_name/change_display_name.html', context)


def merge_accounts(request):
    context = {}
    token = request.GET.get('token')
    if (hasattr(request.user, 'profile')):
        context['profile'] = request.user.profile
    if token is None:
        if 'profile' in context and request.user.profile.is_legit():
            token = ProfileMergeToken(token=str(uuid4()), profile=request.user.profile)
            token.save()
        context['initial'] = True
    else:
        token = ProfileMergeToken.objects.filter(token=token, used=None,
                                                 created__gte=datetime.date.today() - datetime.timedelta(
                                                     hours=1)).first()
        if token is None:
            context['invalid'] = True
            if 'profile' in context and request.user.profile.is_legit():
                token = ProfileMergeToken(token=str(uuid4()), profile=request.user.profile)
                token.save()
            else:
                context['next'] = request.path
        else:
            context['token_profile_social_accounts'] = SocialAccount.objects.filter(user_id=token.profile.user_id)
            context['link'] = reverse('user', args=(token.profile_id,))
    if token and token.token:
        context['next'] = f'{request.path}?token={token.token}'
    context['me'] = reverse('me')
    context['profile_social_accounts'] = SocialAccount.objects.filter(user_id=request.user.id)
    context['token'] = token

    return render(request, 'user/merge/merge_accounts.html', context)


def geotaggers_modal(request, photo_id):
    limit = request.GET.get('limit')
    if limit is not None and limit.isdigit():
        geotags = GeoTag.objects.filter(photo_id=photo_id).order_by('user', '-created').distinct(
            'user').prefetch_related('user')[:int(limit, 10)]
    else:
        geotags = GeoTag.objects.filter(photo_id=photo_id).order_by('user', '-created').distinct(
            'user').prefetch_related('user')
    geotags = sorted(geotags, key=operator.attrgetter('created'), reverse=True)
    geotaggers = []
    if (len(geotags) < 1):
        return HttpResponse('No geotags found for image', status=404)
    for geotag in geotags:
        if geotag.user is None:
            if geotag.origin == GeoTag.REPHOTO or geotag.photo.source is None:
                geotaggers.append({'name': _(dict(geotag.ORIGIN_CHOICES)[geotag.origin]), 'created': geotag.created})
            else:
                geotaggers.append({'name': geotag.photo.source.name, 'created': geotag.created})
        else:
            geotaggers.append(
                {'name': geotag.user.get_display_name, 'geotagger_id': geotag.user_id, 'created': geotag.created})
    context = {
        'geotaggers': geotaggers
    }
    return render(request, 'geotaggers/_geotaggers_modal_content.html', context)


def supporters(request, year=None):
    context = {}
    supporters = {
        'Kulka': {
            'alternate_text': _('KulKa logo'),
            'url': 'https://www.kulka.ee/et' if request.LANGUAGE_CODE == 'et' else 'https://www.kulka.ee/en',
            'img': 'images/logo-kulka_et.png' if request.LANGUAGE_CODE == 'et' else 'images/logo-kulka.png',
        },
        'Ministry of Education': {
            'alternate_text': _('Ministry of Education logo'),
            'url': 'https://www.hm.ee/et' if request.LANGUAGE_CODE == 'et' else 'https://www.hm.ee/en',
            'img': 'images/logo-ministry-of-education-and-research_et.png' if request.LANGUAGE_CODE == 'et'
            else 'images/logo-ministry-of-education-and-research.png',
        },
        'EV100': {
            'alternate_text': _('EV100 logo'),
            'url': 'https://www.ev100.ee/et/ajapaik-selgitame-koos-valja-kuidas-eesti-kohad-aegade-jooksul-muutunud'
            if request.LANGUAGE_CODE == 'et'
            else 'https://www.ev100.ee/en/ajapaik-selgitame-koos-valja-kuidas-eesti-kohad-aegade-jooksul-muutunud',
            'img': 'images/ev100.png'
        },
        'National Foundation of Civil Society': {
            'alternate_text': _('KYSK logo'),
            'url': 'https://www.kysk.ee/est' if request.LANGUAGE_CODE == 'et' else 'https://www.kysk.ee/nfcs',
            'img': 'images/logo-kysk_et.png' if request.LANGUAGE_CODE == 'et' else 'images/logo-kysk.png'
        },
        'Ministry of Culture': {
            'alternate_text': _('Ministry of Culture'),
            'url': 'https://www.kul.ee/et' if request.LANGUAGE_CODE == 'et' else 'https://www.kul.ee/en',
            'img': 'images/logo-ministry-of-culture_et.png' if request.LANGUAGE_CODE == 'et'
            else 'images/logo-ministry-of-culture.png'
        },
        'Republic of Estonia National Heritage Board': {
            'alternate_text': _('Republic of Estonia National Heritage Board'),
            'url': 'https://www.muinsuskaitseamet.ee/et' if request.LANGUAGE_CODE == 'et'
            else 'https://www.muinsuskaitseamet.ee/en',
            'img': 'images/logo-estonian-national-heritage-board_et.png' if request.LANGUAGE_CODE == 'et'
            else 'images/logo-estonian-national-heritage-board.png'
        },
        'Year of Digital Culture 2020': {
            'alternate_text': _('Year of Digital Culture 2020'),
            'url': 'https://www.nlib.ee/et/digikultuur2020' if request.LANGUAGE_CODE == 'et'
            else 'https://www.nlib.ee/en/digikultuur2020',
            'img': 'images/logo-year-of-digital-culture-2020_et.png' if request.LANGUAGE_CODE == 'et'
            else 'images/logo-year-of-digital-culture-2020.png'
        },
        'Wikimedia Finland': {
            'alternate_text': _('Wikimedia Finland'),
            'url': 'https://wikimedia.fi/',
            'img': 'images/logo-wikimedia-finland.png'
        }

    }
    current_supporters = [
        supporters['Wikimedia Finland'],
        supporters['Republic of Estonia National Heritage Board'],
        supporters['Kulka'],
        supporters['Year of Digital Culture 2020']
    ]

    previous_supporters = [
        supporters['Kulka'],
        supporters['Ministry of Culture'],
        supporters['EV100'],
        supporters['Ministry of Education'],
        supporters['National Foundation of Civil Society']
    ]

    supporters = Supporter.objects.all()

    context['current_supporters'] = current_supporters
    context['previous_supporters'] = previous_supporters
    context['supporters'] = supporters

    return render(request, 'donate/supporters.html', context)


def redirect_view(request, photo_id=-1, thumb_size=-1, pseudo_slug=""):
    path = request.path

    if path.find('/ajapaikaja/') == 0:
        request.path = request.path.replace('/ajapaikaja/', '/game/')
    elif path.find('/kaart/') == 0:
        request.path = request.path.replace('/kaart/', '/map/')
    elif path.find('/foto_thumb/') == 0:
        request.path = request.path.replace('/foto_thumb/', '/photo-thumb/')
    elif path.find('/foto_url/') == 0:
        request.path = request.path.replace('/foto_url/', '/photo-thumb/')
    elif path.find('/foto_large/') == 0:
        request.path = request.path.replace('/foto_large/', '/photo-full/')
    elif path.find('/photo-large/') == 0:
        request.path = request.path.replace('/photo-large/', '/photo-full/')
    elif path.find('/photo-url/') == 0:
        request.path = request.path.replace('/photo-url/', '/photo-thumb/')
    elif path.find('/foto/') == 0:
        request.path = request.path.replace('/foto/', "/photo/")
    else:
        request.path = "/"

    response = redirect(request.get_full_path(), permanent=True)
    return response


class MyPasswordSetView(LoginRequiredMixin, PasswordSetView):
    success_url = reverse_lazy('user_settings')


class MyPasswordChangeView(LoginRequiredMixin, PasswordChangeView):
    success_url = reverse_lazy('user_settings')


class MyConnectionsView(LoginRequiredMixin, ConnectionsView):
    success_url = reverse_lazy('user_settings')


class MyEmailView(LoginRequiredMixin, EmailView):
    success_url = reverse_lazy('user_settings')
