/*
 *  OpenChange MAPI library bindings for Perl
 *
 *  Copyright (C) Julien Kerihuel 2007.
 *
 *  This program is free software; you can redistribute it and/or modify
 *  it under the terms of the GNU General Public License as published by
 *  the Free Software Foundation; either version 2 of the License, or
 *  (at your option) any later version.
 *
 *  This program is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 *  GNU General Public License for more details.
 *
 *  You should have received a copy of the GNU General Public License
 *  along with this program; if not, write to the Free Software
 *  Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
 */

%module mapi
%{
typedef unsigned int uint32_t;
typedef unsigned long long uint64_t;
typedef uint64_t NTTIME;
struct ipv4_addr;

#include <libmapi/libmapi.h>
#include <libmapi/proto.h>

%}

%include "typemaps.i"
%include "cstring.i"
%include "cpointer.i"
%include "carrays.i"

%include "../../libmapi/mapidefs.h"
%include "swig_mapitags.h"
%include "swig_mapicodes.h"

%typemap(in) uint8_t = unsigned short;
%apply short { uint8_t };
%typemap(in) uint32_t = unsigned int;
%apply int { uint32_t };


%nodefaultctor SPropTagArray;
%nodefaultdtor SPropTagArray;

%array_functions(uint32_t,aulPropTag);
struct SPropTagArray {
       uint32_t	*aulPropTag;
       uint32_t	cValues;
};

%inline %{
	struct SPropTagArray *new_SPropTagArray(int nb) 
	{
		struct SPropTagArray *result = 0;

		result = (struct SPropTagArray *)(struct SPropTagArray *) calloc(1, sizeof(struct SPropTagArray));
		result->cValues = nb;
		result->aulPropTag = new_aulPropTag(nb);
		      
		return result;
	}

	void delete_SPropTagArray(struct SPropTagArray *s)
	{
		delete_aulPropTag(s->aulPropTag);
		free((struct SPropTagArray *)s);     		
	}
%}


%nodefaultctor mapi_object;
%nodefaultdtor mapi_object;
%inline %{
	mapi_object_t *new_mapi_object()
	{
		mapi_object_t	*obj;

		obj = malloc(sizeof(mapi_object_t));
		mapi_object_init(obj);

		return obj;
	}

	static void delete_mapi_object(mapi_object_t *obj)
	{
		mapi_object_release(obj);
		free((mapi_object_t *)obj);
	}	
%}

struct SRowSet {
	uint32_t cRows;
	struct SRow *aRow;
};

%pointer_functions(uint64_t, int64);
%pointer_functions(uint32_t, int32);
%pointer_functions(struct mapi_SPropValue_array, mapi_SPropValue_array);
%pointer_functions(struct mapi_session *, mapi_session_t);

extern void		lw_dumpdata(void);
extern uint64_t		*lw_getID(struct SRowSet *SRowSet, uint32_t tag, uint32_t idx);

extern uint32_t		MAPIInitialize(const char *profile);
extern void		MAPIUninitialize(void);
extern uint32_t		MapiLogonEx(struct mapi_session **session, char *profile, char *password);
extern uint32_t		GetLastError(void);
extern void		mapi_errstr(const char *function, uint32_t mapi_code);

%cstring_output_allocate(char **s, $1);
extern uint32_t		GetDefaultProfile(const char **s, uint32_t flags);

extern uint32_t		GetDefaultFolder(mapi_object_t *obj, uint64_t *folder, const uint32_t folder_id);
extern uint32_t		OpenFolder(mapi_object_t *obj, uint64_t folder, mapi_object_t *obj2);
extern uint32_t		GetFolderItemsCount(mapi_object_t *obj, uint32_t *unread, uint32_t *total);
extern uint32_t		GetContentsTable(mapi_object_t *obj, mapi_object_t *obj2);
extern uint32_t		SetColumns(mapi_object_t *obj, struct SPropTagArray *lpProps);
extern uint32_t		QueryRows(mapi_object_t *obj, uint32_t nb, uint32_t flg, struct SRowSet *SRowSet);
extern uint32_t		GetRowCount(mapi_object_t *obj, uint32_t *props);
extern uint32_t		OpenMsgStore(mapi_object_t *obj);
extern uint32_t		OpenMessage(mapi_object_t *obj, uint64_t fid, uint64_t mid, mapi_object_t *obj_msg, uint8_t flag);
extern uint32_t		GetPropsAll(mapi_object_t *obj, struct mapi_SPropValue_array *mlpProps);

extern void		mapidump_SPropTagArray(struct SPropTagArray *lpProps);
extern void		mapidump_SRowSet(struct SRowSet *SRowSet, const char *sep);
extern void		mapidump_message(struct mapi_SPropValue_array *mlpProps);
