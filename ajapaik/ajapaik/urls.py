from admin_tools import urls as admin_urls
from allauth import urls as allauth_urls
from django.conf import settings
from django.conf.urls import include, url, i18n
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.contrib.sitemaps import views as sitemap_views
from django.contrib.staticfiles.views import serve
from django.views.generic import RedirectView, TemplateView
from django.views.i18n import JavaScriptCatalog
from django_comments_xtd import urls as dcxtd_urls

from ajapaik.ajapaik import api, delfi, juks, views, iiif
from ajapaik.ajapaik.autocomplete_views import AlbumAutocomplete, AlbumPhotoAutocomplete, AreaAutocomplete, \
    DatingAutocomplete, DatingConfirmationAutocomplete, DeviceAutocomplete, FaceRecognitionRectangleAutocomplete, \
    FaceRecognitionRectangleFeedbackAutocomplete, FaceRecognitionUserSuggestionAutocomplete, \
    FaceRecognitionRectangleSubjectDataSuggestionAutocomplete, GeoTagAutocomplete, \
    GoogleMapsReverseGeocodeAutocomplete, ImageSimilarityAutocomplete, \
    ImageSimilaritySuggestionAutocomplete, LicenceAutocomplete, LocationAutocomplete, \
    ObjectDetectionAnnotationAutocomplete, ObjectAnnotationClassAutocomplete, ObjectAnnotationFeedbackAutocomplete, \
    OpenAlbumAutocomplete, ParentAlbumAutocomplete, PhotoAutocomplete, PointsAutocomplete, ProfileAutocomplete, \
    SkipAutocomplete, SubjectAlbumAutocomplete, SourceAutocomplete, TranscriptionAutocomplete, UserAutocomplete, \
    VideoAutocomplete
from ajapaik.ajapaik.bbox_api import PhotosView
from ajapaik.ajapaik.sitemaps import PhotoSitemap, StaticViewSitemap
from ajapaik.ajapaik_face_recognition import urls as fr_urls
from ajapaik.ajapaik_object_recognition import urls as or_urls
from django.views.decorators.cache import cache_page

urlpatterns = [
    url(r'^stream/', views.fetch_stream, name='fetch_stream'),
    url(r'^difficulty-feedback/', views.difficulty_feedback, name='difficulty_feedback'),
    url(r'^geotag/add/$', views.geotag_add, name='geotag_add'),
    url(r'^geotag/confirm/$', views.geotag_confirm, name='geotag_confirm'),
    url(r'^general-info-modal-content/$', views.get_general_info_modal_content, name='general_info_modal_content'),
    url(r'^info-modal-content/$', views.get_album_info_modal_content, name='info_modal_content'),
    url(r'^game/$', views.game, name='game'),
    url(r'^geotaggers-modal/(?P<photo_id>\d+)/$', views.geotaggers_modal, name='photo_geotaggers'),
    url(r'^geotag/$', views.game, name='game'),
    url(r'^login-modal/$', views.login_modal, name='login_modal'),
    url(r'^map/$', views.mapview, name='map'),
    url(r'^map/photo/(?P<photo_id>\d+)/$', views.mapview, name='map'),
    url(r'^map/rephoto/(?P<rephoto_id>\d+)/$', views.mapview, name='map'),
    url(r'^map/photo/(?P<photo_id>\d+)/rephoto/(?P<rephoto_id>\d+)/$', views.mapview, name='map'),
    url(r'^map-data/$', views.map_objects_by_bounding_box, name='map_objects_by_bounding_box'),
    url(r'^leaderboard/$', views.leaderboard, name='leaderboard'),
    url(r'^leaderboard/album/(?P<album_id>\d+)/$', views.leaderboard, name='album_leaderboard'),
    url(r'^all-time-leaderboard/$', views.all_time_leaderboard, name='all_time_leaderboard'),
    url(r'^top50/$', views.top50, name='top50'),
    url(r'^top50/album/(?P<album_id>\d+)/$', views.top50, name='album_top50'),
    url(r'^photo/(?P<photo_id>\d+)/upload/$', views.rephoto_upload, name='rephoto_upload'),
    url(r'^photo/(?P<photo_id>\d+)/info\.json$', iiif.photo_info, name='iiif_photo_info'),
    url(r'^photo/(?P<photo_id>\d+)/manifest\.json$', iiif.photo_manifest_v2, name='iiif_photo_manifest_v2'),
    url(r'^photo/(?P<photo_id>\d+)/v2/manifest\.json$', iiif.photo_manifest_v2, name='iiif_photo_manifest_v2'),
    url(r'^photo/like/$', views.update_like_state, name='update_like_state'),
    url(r'^photo-upload-modal/(?P<photo_id>\d+)/$', views.photo_upload_modal, name='photo_upload_modal'),
    url(r'^photo/(?P<photo_id>\d+)/$', views.photoslug, name='photo'),
    url(r'^photo/(?P<photo_id>\d+)/(?P<pseudo_slug>.*)/$', views.photoslug, name='photo'),
    url(r'^video/(?P<video_id>\d+)/(?P<pseudo_slug>.*)/$', views.videoslug, name='videoslug'),
    url(r'^video-still/$', views.generate_still_from_video),
    # Legacy URLs
    url(r'^foto_thumb/$', views.redirect_view),
    url(r'^foto_thumb/(?P<photo_id>\d+)/$', views.redirect_view),
    url(r'^foto_thumb/(?P<photo_id>\d+)/(?P<thumb_size>\d+)/', views.redirect_view),
    url(r'^foto_thumb/(?P<photo_id>\d+)/(?P<thumb_size>\d+)/(?P<pseudo_slug>.*)/$', views.redirect_view),
    url(r'^foto_url/(?P<photo_id>\d+)/$', views.redirect_view),
    url(r'^foto_large/(?P<photo_id>\d+)/$', views.redirect_view),
    url(r'^foto_large/(?P<photo_id>\d+)/(?P<pseudo_slug>.*)/$', views.redirect_view),
    url(r'^photo-large/(?P<photo_id>\d+)/$', views.redirect_view),
    url(r'^photo-large/(?P<photo_id>\d+)/(?P<pseudo_slug>.*)/$', views.redirect_view),
    url(r'^photo-url/(?P<photo_id>\d+)/$', views.redirect_view),
    url(r'^photo-url/(?P<photo_id>\d+)/(?P<pseudo_slug>.*)/$', views.redirect_view),
    url(r'^foto/$', views.redirect_view, name='legacy_foto'),
    url(r'^foto/(?P<photo_id>\d+)/$', views.redirect_view, name='legacy_foto'),
    url(r'^foto/(?P<photo_id>\d+)/(?P<pseudo_slug>.*)/$', views.redirect_view, name='legacy_foto'),
    url(r'^ajapaikaja/$', views.redirect_view, name='legacy_game'),
    url(r'^kaart/$', views.redirect_view, name='legacy_map'),
    # Preferred URLs
    url(r'^photo-thumb/(?P<photo_id>\d+)/$', cache_page(86400)(views.image_thumb), name='image_thumb'),
    url(r'^photo-thumb/(?P<photo_id>\d+)/(?P<thumb_size>\d+)/', cache_page(86400)(views.image_thumb), name='image_thumb'),
    url(r'^photo-thumb/(?P<photo_id>\d+)/(?P<thumb_size>\d+)/(?P<pseudo_slug>.*)/$', cache_page(86400)(views.image_thumb),
        name='image_thumb'),
    url(r'^photo-full/(?P<photo_id>\d+)/(?P<pseudo_slug>.*)/$', cache_page(86400)(views.image_full), name='image_full'),
    url(r'^photo-selection/$', views.photo_selection, name='photo_selection'),
    url(r'^view-selection/$', views.list_photo_selection, name='list_photo_selection'),
    url(r'^upload-selection/$', views.upload_photo_selection, name='upload_photo_selection'),
    url(r'^$', views.frontpage, name='frontpage'),
    url(r'^photos/$', views.frontpage, name='frontpage_photos'),
    url(r'^photos/(?P<page>\d+)/$', views.frontpage, name='frontpage_photos'),
    url(r'^photos/(?P<album_id>\d+)/(?P<page>\d+)/$', views.frontpage, name='frontpage_photos'),
    url(r'^frontpage-async/$', views.frontpage_async_data, name='frontpage_async_data'),
    url(r'^frontpage-async-albums/$', views.frontpage_async_albums, name='frontpage_async_albums'),
    url(r'^curator/$', views.curator, name='curator'),
    url(r'^curator-album-info/$', views.curator_get_album_info, name='curator_get_album_info'),
    url(r'^curator-update-my-album/$', views.curator_update_my_album, name='curator_update_my_album'),
    url(r'^curator-album-list/$', views.curator_my_album_list, name='curator_my_album_list'),
    url(r'^curator-selectable-albums/$', views.curator_selectable_albums, name='curator_selectable_albums'),
    url(r'^curator-search/$', views.curator_search, name='curator_search'),
    url(r'^curator-upload/$', views.curator_photo_upload_handler, name='curator_photo_upload_handler'),
    url(r'^public-album-create-handler/$', views.public_add_album, name='public_add_album'),
    url(r'^public-area-create-handler/$', views.public_add_area),
    url(r'^muis-import/$', views.muis_import, name='muis_import'),
    url(r'^csv-import/$', views.csv_import, name='csv_import'),
    url(r'^submit-dating/$', views.submit_dating, name='submit_dating'),
    url(r'^datings/(?P<photo_id>\d+)/$', views.get_datings, name='get_datings'),
    url(r'^donate/$', views.donate, name='donate'),
    url(r'^choose-upload-action/$', views.photo_upload_choice, name='photo_upload_choice'),
    url(r'^user-upload/$', views.user_upload, name='user_upload'),
    url(r'^user-upload-add-album/$', views.user_upload_add_album, name='user_upload_add_album'),
    url(r'^privacy/$', views.privacy, name='privacy'),
    url(r'^terms/$', views.terms, name='terms'),
    url(r'^compare-photos/(?P<photo_id>\d+)/(?P<photo_id_2>\d+)/$', views.compare_photos, name='compare-photos'),
    url(r'^compare-all-photos/$', views.compare_all_photos, name='compare-all-photos'),
    url(r'^compare-all-photos/(?P<photo_id>\d+)/(?P<photo_id_2>\d+)/$', views.compare_all_photos,
        name='compare-all-photos'),
    url(r'^me/$', views.me, name='me'),
    url(r'^user/(?P<user_id>\d+)/$', views.user, name='user'),
    url(r'^user/settings/$', views.user_settings, name='user_settings'),
    url(r'^user/settings-modal/$', views.user_settings_modal, name='user_settings_modal'),
    url(r'^user/merge-accounts/$', views.merge_accounts, name='merge_accounts'),
    url(r'^photo-upload/settings-modal/$', views.rephoto_upload_settings_modal, name='rephoto_upload_settings_modal'),
    url(r'^supporters/$', views.supporters, name='supporters'),
    url(r'^accounts/launcher/$', views.oauthdone, name='oauthdone'),
]

urlpatterns += [
    url(r'^api/v1/login/$', api.Login.as_view()),
    url(r'^api/v1/register/$', api.Register.as_view(), name='api_register'),
    url(r'^api/v1/logout/$', api.api_logout.as_view(), name='api_logout'),
    url(r'^api/v1/user/me/$', api.api_user_me.as_view()),
    url(r'^api/v1/album/(?P<album_id>\d+)/$', api.AlbumNearestPhotos.as_view()),
    url(r'^api/v1/album/(?P<album_id>\d+)/information/$', api.AlbumInformation.as_view(), name='api_album_information'),
    url(
        r'^api/v1/album/(?P<album_id>\d+)/photo/(?P<photo_id>\d+)/$',
        api.AlbumPhotoInformation.as_view(),
        name='api_albumphoto_information'
    ),
    url(r'^api/v1/album/nearest/$', api.AlbumNearestPhotos.as_view()),
    url(r'^api/v1/finna/nearest/$', api.FinnaNearestPhotos.as_view()),
    url(r'^api/v1/album/state/$', api.AlbumPhotos.as_view()),
    url(r'^api/v1/source/$', api.SourceDetails.as_view()),
    url(r'^api/v1/album/photos/search/$', api.PhotosInAlbumSearch.as_view()),
    url(r'^api/v1/album_thumb/(?P<album_id>\d+)/$', api.api_album_thumb, name='api_album_thumb'),
    url(r'^api/v1/album_thumb/(?P<album_id>\d+)/(?P<thumb_size>.*)/$', api.api_album_thumb, name='api_album_thumb'),
    url(r'^api/v1/albums/$', api.AlbumList.as_view()),
    url(r'^api/v1/albums/search/$', api.AlbumsSearch.as_view()),
    url(r'^api/v1/wikidocumentaries/$', api.WikidocsAlbumsSearch.as_view()),
    url(r'^api/v1/wikidocumentaries/photos/$', api.WikidocsAlbumSearch.as_view()),
    url(r'^api/v1/photo/applied-operations/$', api.PhotoAppliedOperations.as_view(),
        name='api_photo_applied_operations'),
    url(r'^api/v1/photo/applied-operations/(?P<photo_id>\d+)/$', api.PhotoAppliedOperations.as_view(),
        name='api_photo_applied_operations'),
    url(r'^api/v1/photo/suggestion/$', api.PhotoSuggestion.as_view(), name='api_photo_suggestion'),
    url(r'^api/v1/photo/suggestion/(?P<photo_id>\d+)/$', api.PhotoSuggestion.as_view(), name='api_photo_suggestion'),
    url(r'^api/v1/photo/state/$', api.PhotoDetails.as_view()),
    url(r'^api/v1/photo/upload/$', api.RephotoUpload.as_view(), name='api_photo_upload'),
    url(r'^api/v1/photo/upload/settings$', api.RephotoUploadSettings.as_view(),
        name='api_submit_rephoto_upload_settings'),
    url(r'^api/v1/photo/favorite/set/$', api.ToggleUserFavoritePhoto.as_view()),
    url(r'^api/v1/photo/fetch-hkm-finna/$', api.FetchFinnaPhoto.as_view()),
    url(r'^api/v1/photos/favorite/order-by-distance-to-location/$', api.UserFavoritePhotoList.as_view()),
    url(r'^api/v1/photos/filtered/rephotographed-by-user/$', api.PhotosWithUserRephotos.as_view()),
    url(r'^api/v1/photos/search/$', api.PhotosSearch.as_view()),
    url(r'^api/v1/photos/search/user-rephotos/$', api.UserRephotosSearch.as_view()),
    url(r'^api/v1/photos/similar/$', api.SubmitSimilarPhotos.as_view(), name='api_submit_similarity'),
    url(r'^api/v1/transcriptions/(?P<photo_id>\d+)/$', api.Transcriptions.as_view(), name='api_transcriptions'),
    url(r'^api/v1/transcriptions/$', api.Transcriptions.as_view(), name='api_submit_transcription'),
    url(r'^api/v1/transcription-feedback/$', api.SubmitTranscriptionFeedback.as_view(),
        name='api_confirm_transcription'),
    url(r'^api/v1/change-profile-display-name$', api.ChangeProfileDisplayName.as_view(),
        name='api_change_profile_display_name'),
    url(r'^api/v1/user-settings/$', api.UserSettings.as_view(), name='api_submit_user_settings'),
    url(r'^api/v1/merge-profiles/$', api.MergeProfiles.as_view(), name='api_merge_users')
]

urlpatterns += [
    url(r'^delfi-api/v1/photo/$', delfi.photo_info),
    url(r'^delfi_api/v1/photo/$', delfi.photo_info),
    url(r'^delfi-api/v1/photos-bbox/$', delfi.photos_bbox),
    url(r'^delfi_api/v1/photos_bbox/$', delfi.photos_bbox),
]

urlpatterns += [
    url(r'^bbox/v1/$', PhotosView.as_view())
]

urlpatterns += [
    url(r'^juks/empty-json/$', juks.empty_json),
    url(r'^juks/layers/$', juks.layers),
]

sitemaps = {
    'photo_permalinks': PhotoSitemap,
    'static_pages': StaticViewSitemap
}

urlpatterns += [
    url(r'^%s(?P<path>.*)$' % settings.STATIC_URL.lstrip('/'), cache_page(86400)(serve), {'show_indexes': True, 'insecure': False}),
    url(r'^accounts/email/$', views.MyEmailView.as_view(), name="account_email"),
    url(r'^accounts/password/change/$', views.MyPasswordChangeView.as_view(), name="account_change_password"),
    url(r'^accounts/password/set/$', views.MyPasswordSetView.as_view(), name="account_set_password"),
    url(r'^accounts/social/connections/$', views.MyConnectionsView.as_view(), name="socialaccount_connections"),
    url(r'^accounts/', include(allauth_urls)),
    url(r'^admin/', admin.site.urls),
    url(r'^admin_tools/', include(admin_urls)),
    url(r'^comments/for/(?P<photo_id>\d+)/$', views.CommentList.as_view(), name='comments-for-photo'),
    url(r'^comments/post-one/(?P<photo_id>\d+)/$', login_required(views.PostComment.as_view()),
        name='comments-post-one'),
    url(r'^comments/delete-one/$', login_required(views.DeleteComment.as_view()), name='comments-delete-one'),
    url(r'^comments/edit-one/$', login_required(views.EditComment.as_view()), name='comments-edit-one'),
    url(r'^comments/like-count/(?P<comment_id>\d+)/$', views.get_comment_like_count, name='comments-like-count'),
    url(r'^comments/', include(dcxtd_urls)),
    url(r'^i18n/', include(i18n)),
    url(r'^jsi18n/$', cache_page(86400)(JavaScriptCatalog.as_view(packages=['ajapaik.ajapaik'], domain='djangojs')),
        name='javascript-catalog'),
    url(r'^favicon\.ico$', RedirectView.as_view(url='/static/images/favicon.ico', permanent=True)),
    url(r'^feed/photos/', RedirectView.as_view(url='http://api.ajapaik.ee/?action=photo&format=atom', permanent=True),
        name='feed'),
    url(r'^sitemap\.xml$', cache_page(86400)(sitemap_views.index), {'sitemaps': sitemaps}),
    url(r'^sitemap-(?P<section>.+).xml$', cache_page(86400)(sitemap_views.sitemap), {'sitemaps': sitemaps},
        name='django.contrib.sitemaps.views.sitemap'),
    url(r'^face-recognition/', include(fr_urls)),
    url(r'^object-recognition/', include(or_urls))
]

if hasattr(settings, 'GOOGLE_ANALYTICS_KEY') and settings.GOOGLE_ANALYTICS_KEY == 'UA-21689048-1':
    urlpatterns += [
        url(r'^robots\.txt$', TemplateView.as_view(template_name='robots.txt', content_type='text/plain')),
    ]
else:
    urlpatterns += [
        url(r'^robots\.txt$', TemplateView.as_view(template_name='robots-staging.txt', content_type='text/plain')),
    ]

if settings.DEBUG:
    import debug_toolbar

    urlpatterns += [
        url(r'^__debug__/', include(debug_toolbar.urls)),
    ]
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(r'^vanalinnad.mooo.com/(.*)', document_root=settings.VANALINNAD_ROOT)

urlpatterns += [
    url(r'^autocomplete/album-autocomplete/$', AlbumAutocomplete.as_view(), name='album-autocomplete'),
    url(r'^autocomplete/album-photo-autocomplete/$', AlbumPhotoAutocomplete.as_view(), name='album-photo-autocomplete'),
    url(r'^autocomplete/area-autocomplete/$', AreaAutocomplete.as_view(), name='area-autocomplete'),
    url(r'^autocomplete/dating-autocomplete/$', DatingAutocomplete.as_view(), name='dating-autocomplete'),
    url(r'^autocomplete/dating_confirmation-autocomplete/$', DatingConfirmationAutocomplete.as_view(),
        name='dating-confirmation-autocomplete'),
    url(r'^autocomplete/device-autocomplete/$', DeviceAutocomplete.as_view(), name='device-autocomplete'),
    url(r'^autocomplete/face-recognition-rectangle-autocomplete/$', FaceRecognitionRectangleAutocomplete.as_view(),
        name='face-recognition-rectangle-autocomplete'),
    url(r'^autocomplete/face-recognition-rectangle-feedback-autocomplete/$',
        FaceRecognitionRectangleFeedbackAutocomplete.as_view(),
        name='face-recognition-rectangle-feedback-autocomplete'),
    url(r'^autocomplete/face-recognition-user-suggestion-autocomplete/$',
        FaceRecognitionUserSuggestionAutocomplete.as_view(), name='face-recognition-user-suggestion-autocomplete'),
    url(r'^autocomplete/face-recognition-rectangle-subject-data-suggestion-autocomplete/$',
        FaceRecognitionRectangleSubjectDataSuggestionAutocomplete.as_view(),
        name='face-recognition-rectangle-subject-data-suggestion-autocomplete'),
    url(r'^autocomplete/geotag-autocomplete/$', GeoTagAutocomplete.as_view(), name='geotag-autocomplete'),
    url(r'^autocomplete/google-reverse-geocode-autocomplete/$',
        GoogleMapsReverseGeocodeAutocomplete.as_view(),
        name='google-reverse-geocode-autocomplete'
        ),
    url(r'^autocomplete/image-similarity-autocomplete/$', ImageSimilarityAutocomplete.as_view(),
        name='image-similarity-autocomplete'),
    url(r'^autocomplete/image-similarity-suggestion-autocomplete/$', ImageSimilaritySuggestionAutocomplete.as_view(),
        name='image-similarity-suggestion-autocomplete'),
    url(r'^autocomplete/location/$', LocationAutocomplete.as_view(), name='location-autocomplete'),
    url(r'^autocomplete/licence-autocomplete/$', LicenceAutocomplete.as_view(), name='licence-autocomplete'),
    url(r'^autocomplete/object-detection-annotation-autocomplete/$', ObjectDetectionAnnotationAutocomplete.as_view(),
        name='object-detection-annotation-autocomplete'),
    url(r'^autocomplete/object-annotation-class-autocomplete/$', ObjectAnnotationClassAutocomplete.as_view(),
        name='object-annotation-class-autocomplete'),
    url(r'^autocomplete/object-annotation-feedback-autocomplete/$', ObjectAnnotationFeedbackAutocomplete.as_view(),
        name='object-annotation-feedback-autocomplete'),
    url(r'^autocomplete/photo-autocomplete/$', PhotoAutocomplete.as_view(), name='photo-autocomplete'),
    url(r'^autocomplete/points-autocomplete/$', PointsAutocomplete.as_view(), name='points-autocomplete'),
    url(r'^autocomplete/profile-autocomplete/$', ProfileAutocomplete.as_view(), name='profile-autocomplete'),
    url(r'^autocomplete/open-album-autocomplete/$', OpenAlbumAutocomplete.as_view(), name='open-album-autocomplete'),
    url(r'^autocomplete/parent-album-autocomplete/$', ParentAlbumAutocomplete.as_view(),
        name='open-album-autocomplete'),
    url(r'^autocomplete/skip-autocomplete/$', SkipAutocomplete.as_view(), name='skip-autocomplete'),
    url(r'^autocomplete/source-autocomplete/$', SourceAutocomplete.as_view(), name='source-autocomplete'),
    url(r'^autocomplete/subject-album-autocomplete/$', SubjectAlbumAutocomplete.as_view(),
        name='subject-album-autocomplete'),
    url(r'^autocomplete/transcription-autocomplete/$', TranscriptionAutocomplete.as_view(),
        name='transcription-autocomplete'),
    url(r'^autocomplete/user-autocomplete/$', UserAutocomplete.as_view(), name='user-autocomplete'),
    url(r'^autocomplete/video-autocomplete/$', VideoAutocomplete.as_view(), name='video-autocomplete'),
]
