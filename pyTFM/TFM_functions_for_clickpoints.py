﻿### function integrating Traktion force microscopy into a clcikpoints database

from pyTFM.grid_setup_solids_py import *
from pyTFM.functions_for_cell_colonie import *
from pyTFM.solids_py_stress_functions import *
from pyTFM.utilities_TFM import *
from pyTFM.TFM_functions import *
from pyTFM.parameters_and_strings import *
from pyTFM.frame_shift_correction import *
import solidspy.postprocesor as pos
import solidspy.assemutil as ass
import solidspy.solutil as sol
from peewee import IntegrityError
import clickpoints
from skimage.morphology import label
from skimage.filters import threshold_otsu
import os
import re
import warnings
from tqdm import tqdm


class Mask_Error(Exception):
    pass
class ShapeMismatchError(Exception):
    pass
class cells_masks():
    def __init__(self,frames,db,db_info,parameter_dict):
        self.frames=[]
        self.db=db
        self.db_info=db_info
        self.parameter_dict=parameter_dict
        self.indices = {m.name: m.index for m in self.db.getMaskTypes()}
        self._masks_dict= defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: None)))
        self._warns_dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: "")))
        self.warn_thresh=1000
        self.add_frames(frames,min_size=parameter_dict["min_obj_size"])
    # dictionaries properties because iterating through these dictionaries often adds empty entries,due to the
    # default dict structure. This will for example generate empty cell (cell colonies in a frame when trying to load a
    # mask that is not exisiting
    @property
    def masks_dict(self):
        return copy.deepcopy(self._masks_dict)
    @property
    def warns_dict(self):
        return copy.deepcopy(self._warns_dict)


    def add_frames(self,frames,min_size=1000):
        print("loading masks: ")
        frames = make_iterable(frames)
        for frame in tqdm(frames):
            mask = try_mask_load(self.db,self.db_info["frames_ref_dict"][frame],raise_error=False,mtype="all") #loading mask
            if not isinstance(mask,np.ndarray): # checking if mask instance could be loaded
                for mask_name, index in self.indices.items():
                    self._masks_dict[frame][0]["mask_name"] = None  # writing to dict
                    self._warns_dict[frame][0]["mask_name"] = "no mask found"
                continue
            # optional cutting close to image edge
            mask_full = mask > 0  # mask as one block
            mask_full = binary_fill_holes(mask_full) #fillingthe whole area
            mask_cut, warn_edge = cut_mask_from_edge_wrapper(self.parameter_dict["edge_padding"],mask, self.parameter_dict, cut=True) # just for the warning here
            mask_full = remove_small_objects(mask_full, min_size=min_size)  # cleanup
            mask_full = label(mask_full) # finding individual objects
            regions = regionprops(mask_full)

            if len(regions)==0: # checking if mask is completely empty
                for mask_name, index in self.indices.items():
                    self._masks_dict[frame][0][mask_name] = None  # writing to dict
                    self._warns_dict[frame][0][mask_name] = "no mask found"
                    self._masks_dict[frame][obj_id]["com"] = r.centroid
                continue

            for obj_id, r in enumerate(regions): #iterating through all objects
                for mask_name,index in self.indices.items(): # iterating through all max types
                    shape = mask.shape
                    new_mask = np.zeros(shape)
                    new_mask[r.coords[:,0],r.coords[:,1]]=1
                    coords_final=np.where(np.logical_and(mask==index,new_mask))
                    # extracting only one mask type in only one object
                    warn=check_mask_size(mask,self.warn_thresh,print_out=True,mask_name=mask_name,frame=frame)
                    self._masks_dict[frame][obj_id][mask_name]=(coords_final,shape) # writing to dict only the coordinates of true values
                    self._warns_dict[frame][obj_id][mask_name]=warn + " " * (warn!="") + warn_edge
                    self._masks_dict[frame][obj_id]["com"]=r.centroid


    def get_com_frame(self,frame):
        ret=[]
        for cell_id in self.masks_dict[frame].keys():
            com = self.masks_dict[frame][cell_id]["com"]  # do i need to use the property here??
            ret.append((cell_id,com))
        return ret

    def reconstruct_mask(self,frame,cell_id,mtype,raise_error=True,fill_holes=False,cut_close_to_edge=True,custom_cut_facor=None): # takes about 0.2 seconds for one type of mask
        #note: small objects have already been removed
        mask=None
        ind_shape=self.masks_dict[frame][cell_id][mtype] # do i need to use the property here??
        if isinstance(ind_shape,tuple):
            indices=ind_shape[0]
            shape=ind_shape[1]
            if isinstance(indices,tuple) and isinstance(shape,tuple):
                if len(indices[0])>0: # can also be empty tuple sometimes
                    mask=np.zeros(shape)
                    mask[indices]=1
                    if fill_holes:
                        mask = binary_fill_holes(mask)
                    if cut_close_to_edge:
                        mask, warn_edge = cut_mask_from_edge_wrapper(self.parameter_dict["edge_padding"] ,mask, self.parameter_dict, cut=True)
                    return mask.astype(bool)
        else:
            if raise_error:
                raise Mask_Error("no mask found in frame %s for type %s" % (str(frame), mtype))
        return mask


    def reconstruct_masks_frame(self,frame,mtype,raise_error=True,fill_holes=False,obj_ids=[]):
        # retrieves the a list of all masks and cell ids in one frame
        ret=[]
        mtypes=make_iterable(mtype)
        for cell_id in self.masks_dict[frame].keys():
            if cell_id in obj_ids or len(obj_ids)==0: # only select certain objects
                for mtype in mtypes:
                    mask=self.reconstruct_mask(frame, cell_id, mtype, raise_error=raise_error, fill_holes=fill_holes)
                    warn=self.get_warning(frame,cell_id,mtype)
                    ret.append((cell_id,mask,mtype,warn))
        return ret
    def reconstruct_masks_frame_add(self,frame,mtypes,raise_error=True,fill_holes=False,obj_ids=[]):
        ret=self.reconstruct_masks_frame(frame,mtypes,raise_error=raise_error,fill_holes=fill_holes,obj_ids=obj_ids)
        shape=[mask.shape for cell_id,mask,mtype,warn in ret if isinstance(mask,np.ndarray)]
        if len(shape)==0:
            return ret
        ret_dict = defaultdict(lambda: np.zeros(shape[0]).astype(bool))
        warn_dict = defaultdict(lambda: "")
        if len(ret)>1:
            for cell_id,mask,mtype,warn in ret:
                if not isinstance(mask, np.ndarray):
                    continue
                ret_dict[cell_id]=np.logical_or(ret_dict[cell_id],mask)
                if warn not in warn_dict[cell_id]:
                    warn_dict[cell_id]+= ", " + warn
        ret_final=[(cell_id,mask,"SUM",warn) for (cell_id,mask),warn in zip(ret_dict.items(),warn_dict.values())]
        ret_final=ret if len(ret_final)==0 else ret_final
        return ret_final

    def get_warning(self,frame,cell_id,mtype):
        return(self.warns_dict[frame][cell_id][mtype])





def check_mask_size(mask,warn_tresh,print_out=True,mask_name="",frame=""):

    warn="" if np.sum(mask.astype(bool))>warn_tresh else "small mask"
    if print_out and warn!="":
        print(warn + " of type %s in %s" % (mask_name, frame))
    return warn

def write_output_file(values,value_type, file_path,new_file=False):
    if new_file:
        if os.path.exists(file_path): # try some other out names when one already exists
            for i in range(100000):
                file_path=os.path.join(os.path.split(file_path)[0],"out"+str(i)+".txt")
                if not os.path.exists(file_path):
                    break
    if value_type=="parameters":
        with open(file_path, "w+") as f:
            f.write("analysis_paramters")
            for parameter, value in values.items():
                if parameter not in ["cut_instruction","mask_properties","FEM_mode_id"]:
                    f.write(parameter + "\t" + str(value)+ "\n")
    if value_type == "results":
        # list of available frames sorted
        frames=list(values.keys())
        frames.sort(key=lambda x:int(x)) # extra sorting step
        with open(file_path, "a+") as f:
            for frame in frames:
                for name,res_list in values[frame].items():
                    for res_single in res_list:
                        cell_id, res, warn = res_single
                        f.write(frame + "\t"+ str(cell_id)+ "\t" + name + "\t" + str(round_flexible(res)) + "\t" + units[
                            name] + "\t" * (warn != "") + warn + "\n")
    return file_path

def except_error(func, error,print_error=True, **kwargs):  # take functino and qkwarks
    '''
    wraper to handle errors and return false if the exception is encountered
    :param func:
    :param error:
    :param kwargs:
    :param return_values:
    :return:
    '''

    try:
        values = func(**kwargs)
    except error as e:
        if print_error:
            print(e)
        return False
    return values

def check_shape(x,y):
    s1=np.array(x.shape)
    s2=np.array(y.shape)
    if not all(s1==s2):
        raise ShapeMismatchError("shape of input arrays is unequal. Try recalculating the corresponding arrays.")


def try_mask_load(db,frame,raise_error=True,mtype="cell colony",ret_type="None"):
    try:
        mask = db.getMask(frame=frame, layer=1).data
        # extract only one type of mask
        if not mtype=="all":
            index=db.getMaskType(mtype).index
            mask = mask == index
    # raising error if no mask object in clickpoints exist
    except AttributeError:
        if raise_error:
            raise Mask_Error("no mask found in frame %s for type %s" % (str(frame), mtype))
        else:
            if ret_type=="zeros":
                return np.zeros(db.getImage(frame=frame).data.shape)
            else:
                return None
    return mask


def warn_small_FEM_area(mask_area,threshold):
    warn=""
    area=np.sum(mask_area)
    if area<threshold:
        warnings.warn("FEM grid is very small (%d pixel). Consider increasing resolution of deformation and traction field."%area)
        warn="small FEM grid"
    return warn

def check_empty_mask(mask,mtype="---",frame="---",cell_id="---",add_str_error=""):
    if not isinstance(mask,np.ndarray):
        raise Mask_Error("mask empty for mask type %s in frame %s for patch %s" % (str(mtype), str(frame), str(cell_id)) + " " + add_str_error)

def check_small_or_empty_mask(mask,frame, mtype,warn_thresh=None,raise_error=True, add_str_error="",add_str_warn=""):
    # checking if mask is empty
    warn=""
    if np.sum(mask)==0:
        if raise_error:
            raise Mask_Error("mask empty for mask type %s in frame %s" % (mtype,str(frame)) + " " + add_str_error)
    # checking if mask is suspiciously small
    elif isinstance(warn_thresh,(int,float)):
        if np.sum(binary_fill_holes(mask))<warn_thresh:
            print("mask for %s is very small"%mtype + add_str_error)
            warn= "selected area is very small" + " " + add_str_error
    return warn




def try_to_load_deformation(path, frame, warn=False):
    '''
    loading the deformations fro a given frame. If deformations are not found either raises an error
    or a warning (warn=True) and returns None for u and v

    :param path:
    :param frame:
    :param warn:
    :return:
    '''
    try:
        u = np.load(os.path.join(path, frame + "u.npy"))
        v = np.load(os.path.join(path, frame + "v.npy"))
    except FileNotFoundError:
        if warn:
            warnings.warn("no deformations found for frame " + frame)
        else:
            raise FileNotFoundError("no deformations found for frame " + frame)
        return (None, None)
    return (u, v)



def try_to_load_traction(path, frame, warn=False):
    '''
    loading the tractions from a given frame. If tractions are not found either raises an error
    or a warning (warn=True) and returns None for u and v

    :param path:
    :param frame:
    :param warn:
    :return:
    '''
    try:
        t_x = np.load(os.path.join(path, frame + "tx.npy"))
        t_y = np.load(os.path.join(path, frame + "ty.npy"))
    except FileNotFoundError as e:
        if warn:
            warnings.warn("no traction forces found for frame " + frame)
        else:
            raise FileNotFoundError("no traction forces found for frame " + frame)
        return (None, None)
    return (t_x, t_y)





def create_layers_on_demand(db,db_info, layer_list):

    '''
    :param db: clickpointsw database
    :param layer_list: list of layer names that should be created
    :return:
    '''
    layer_list=make_iterable(layer_list)
    if any([l not in db_info["layers"] for l in layer_list]):
        base_layer = db.getLayer(id=1)
        for pl in layer_list:
            if pl not in db_info["layers"]:
                db.getLayer(pl, base_layer=base_layer, create=True)



def split_dict_str(string):
    string=string.strip("{").strip("}")
    string_list=string.split(",")
    dict_obj={}
    for e in string_list:
        key,value=[sub_str.strip(" ") for sub_str in e.split(":")]
        dict_obj[try_int_strip(key)]=try_int_strip(value)
    return dict_obj

def split_list_str(string):
    string=string.strip("[").strip("]")
    string_list=string.split(" ")
    list_obj=[try_int_strip(v) for v in string_list]
    return list_obj

def get_option_wrapper(db,key,unpack_funct=None,empty_return=list):
    try:
        if unpack_funct:
            return unpack_funct(db.table_option.select().where(db.table_option.key == key).get().value)
        else:
            return db.table_option.select().where(db.table_option.key == key).get().value
    except:
        return empty_return()


def get_db_info_for_analysis(db):

    unique_frames = get_option_wrapper(db,"unique_frames",split_list_str)
    file_order = get_option_wrapper(db,"file_order",split_dict_str)
    frames_ref_dict = get_option_wrapper(db,"frames_ref_dict",split_dict_str,empty_return=dict)
    id_frame_dict = get_option_wrapper(db,"id_frame_dict",split_dict_str,empty_return=dict)

    cbd_frames_ref_dict = {value:key for key, value in frames_ref_dict.items()} # inverse of frames_ref_dict

    layers=[l.name for l in db.getLayers()]
    try:
        path = get_option_wrapper(db,"folder",None)
    except:
        path = db.getPath(id=1).path
    if path==".": # if empty path object in clickpoints use the path where clickpoints is saved
        path=os.path.split(db._database_filename)[0]
    im_shapes={} #exact list of image shapes

    for frame in unique_frames:
        im_shapes[frame]=db.getImages(frame=frames_ref_dict[frame])[0].data.shape

    mask_types=[m.name for m in db.getMaskTypes()] # list mask types
    db_info = {"file_order": file_order,
               "frames_ref_dict": frames_ref_dict,
               "path": path,
               "im_shape": im_shapes,
               "mask_types":mask_types,
               "layers":layers,
               "unique_frames": unique_frames,
               "id_frame_dict":id_frame_dict,
               "cbd_frames_ref_dict":cbd_frames_ref_dict
               }
    return db_info, unique_frames


def add_plot(plot_type, values,plot_function,frame,db_info,default_fig_parameters,parameter_dict,db):
    #values: values (args) that are needed as input for the plotting functions
    if plot_type in default_fig_parameters["plots"][parameter_dict["FEM_mode"]]:  # checking if this should be plotted
        layer = default_fig_parameters["plots_layers"][plot_type]
        file_name=default_fig_parameters["file_names"][plot_type]

        create_layers_on_demand(db, db_info, [layer])
        plt.ioff()
        dpi = 200
        fig_parameters = set_fig_parameters(db_info["defo_shape"], db_info["im_shape"][frame], dpi,
                                            default_fig_parameters,
                                            figtype=plot_type)
        fig,ax = plot_function(*values, **fig_parameters)

        # saving the the plot
        print("saving to "+os.path.join(db_info["path"], frame + file_name))
        fig.savefig(os.path.join(db_info["path"], frame + file_name), dpi=200)
        plt.close(fig)
        # adding the plot to the database
        except_error(db.setImage, IntegrityError, print_error=True, filename=frame + file_name,
                     layer=layer, path=1,sort_index=db_info["frames_ref_dict"][frame])



def sum_on_area(frame,res_dict,parameter_dict,label,masks,mask_types=None,obj_ids=[],x=None,y=None,sumtype="abs",add_cut_factor=None):
    fill_holes=True if parameter_dict["FEM_mode"]=="colony" else False
    mask_iter=masks.reconstruct_masks_frame(frame, mask_types,obj_ids=obj_ids, raise_error=False, fill_holes=fill_holes)
    for obj_id,mask,mtype,warn in mask_iter:
        if not isinstance(mask,np.ndarray):
            print("couldn't identify mask %s in frame %s patch %s" % (str(mtype),str(frame), str(obj_id)))
            continue
        if isinstance(add_cut_factor,float): # additional cutting when in layer-mode
            mask,w=cut_mask_from_edge(mask,add_cut_factor,parameter_dict["TFM_mode"]=="colony")
        check_empty_mask(mask, mtype, frame, obj_id)
        label2=default_parameters["mask_properties"][mtype]["label"]
        if sumtype=="abs":
            mask_int = interpolation(mask, dims=x.shape, min_cell_size=100)
            res_dict[frame]["%s on %s"%(label,label2)].append([obj_id,np.sum(np.sqrt(x[mask_int] ** 2 + y[mask_int] ** 2)),warn])
        if sumtype=="mean":
            mask_int = interpolation(mask, dims=x.shape, min_cell_size=100)
            res_dict[frame]["%s on %s" % (label, label2)].append([obj_id,np.mean(x[mask_int]),warn])
        if sumtype=="area": # area of original mask, without interpolation
            area = np.sum(mask) * ((parameter_dict["pixelsize"] * 10 ** -6) ** 2)
            res_dict[frame]["%s of %s" % (label, label2)].append([obj_id,area,warn])




def general_properties(frame, parameter_dict,res_dict, db,db_info=None,masks=None, **kwargs):
    '''
    Number of cells, area of the cell colony...
    :param frame:
    :param parameter_dict:
    :param res_dict:
    :param db:
    :return:
    '''

    # area of the cells/cell colony
    # retrieve which masks should be used to calculate the area
    use_type="area_colony" if parameter_dict["TFM_mode"]=="colony" else "area_layer"
    mtypes = [m for m in db_info["mask_types"] if m in get_masks_by_key(default_parameters,"use",use_type)]
    # place holder for shape if not defined in defo-shape, needed for counting cells
    int_shape=db_info["defo_shape"] if "defo_shape" in db_info.keys() else (int(db_info["im_shape"][frame][0] * 0.2),
                                                                            int(db_info["im_shape"][frame][1] * 0.2))


    # calculate the area of each mask
    sum_on_area(frame, res_dict, parameter_dict,"area", masks,mask_types=mtypes, sumtype="area")
    # calculating the cell count for each colony
    mask_iter = masks.reconstruct_masks_frame(frame, "membrane", raise_error=True, fill_holes=False)
    for obj_id,mask_membrane,mtype,warn in mask_iter:
        if not isinstance(mask_membrane, np.ndarray): # this could be improved maybe....
            print("couldn't identify cell borders in frame %s patch %s" % (str(frame), str(obj_id)))
            continue
        borders = find_borders(mask_membrane, int_shape, raise_error=False, type=parameter_dict["FEM_mode"])
        if not isinstance(borders, Cells_and_Lines):
            print("couldn't identify cell borders in frame %s patch %s" % (str(frame), str(obj_id)))
            continue
        n_cells=borders.n_cells
        res_dict[frame]["colony n_cells"].append([obj_id,n_cells,warn])

    # center of mass 8centroid) of each object// a simple check if objects have been identified correctly
    for obj_id, com in masks.get_com_frame(frame):
        res_dict[frame]["center of object"].append([obj_id,str(np.round(com,2)),""])


def find_full_im_path(cdb_image, base_folder):
    rel_path = cdb_image.path.path
    filename = cdb_image.filename
    return os.path.join(os.path.join(base_folder, rel_path), filename)


def simple_shift_correction(frame, parameter_dict,res_dict, db,db_info=None,**kwargs):
    #load images from database
    im_a=db.getImage(id=db_info["file_order"][frame + "images_after"])
    im_b=db.getImage(id=db_info["file_order"][frame + "images_before"])
    im_m=db.getImage(id=db_info["file_order"][frame + "membranes"])
    image_after=im_a.data
    image_before=im_b.data
    image_membrane= im_m.data
    # get paths for saving later
    im_a_path = find_full_im_path(im_a,db_info["path"])
    im_b_path = find_full_im_path(im_b,db_info["path"])
    im_m_path = find_full_im_path(im_m,db_info["path"])

    # find shift with image registration
    shift_values = register_translation(image_before, image_after, upsample_factor=100)
    shift_y = shift_values[0][0]
    shift_x = shift_values[0][1]

    # using interpolation to shift subpixel precision
    img_shift_b = shift(image_before, shift=(-shift_y, -shift_x), order=5)
    img_shift_bf = shift(image_membrane, shift=(-shift_y, -shift_x), order=5)

    b = normalizing(croping_after_shift(img_shift_b, shift_x, shift_y))
    a = normalizing(croping_after_shift(image_after, shift_x, shift_y))
    bf = normalizing(croping_after_shift(img_shift_bf, shift_x, shift_y))
    # saving images
    b_save = Image.fromarray(b * 255)
    a_save = Image.fromarray(a * 255)
    bf_save = Image.fromarray(bf * 255)
    print("save im b:",im_b_path,"save im a:",im_a_path,"save im m:",im_m_path )
    b_save.save(im_b_path)
    a_save.save(im_a_path)
    bf_save.save(im_m_path)

def simple_segmentation(frame, parameter_dict,res_dict, db,db_info=None,masks=None,seg_threshold=0,seg_type="cell_area",im_filter=None,**kwargs):

    if seg_type == "cell_area":
        mtypes = get_masks_by_key(default_parameters, "use", "stress_layer")
        if all([m in db_info["mask_types"] for m in mtypes]):
            im=db.getImage(id=db_info["file_order"][frame + "membranes"]).data
            if not isinstance(im_filter,np.ndarray):
                im_filter=gaussian_filter(im,sigma=10)
            mask=im_filter > seg_threshold
            og_mask=try_mask_load(db,db_info["frames_ref_dict"][frame],raise_error=False,mtype="all",ret_type="zeros")
            memb_mask = og_mask==parameter_dict["mask_properties"]["membrane"]["index"]
            og_mask[mask] = 1
            og_mask[~mask] = 2
            og_mask[memb_mask]=parameter_dict["mask_properties"]["membrane"]["index"]
            db.setMask(frame=db_info["frames_ref_dict"][frame],data=og_mask.astype("uint8"))
    if seg_type =="membrane":
        mtypes = get_masks_by_key(default_parameters, "use", "borders")
        if all([m in db_info["mask_types"] for m in mtypes]):
            im = db.getImage(id=db_info["file_order"][frame + "membranes"]).data.astype(float)
            if not isinstance(im_filter, np.ndarray):
                im_filter =  gaussian_filter(im, sigma=2)-gaussian_filter(im, sigma=10)
            mask = im_filter > seg_threshold
            mask=remove_small_objects(mask,min_size=1000) # same as when actually preparing borders
            og_mask = try_mask_load(db, db_info["frames_ref_dict"][frame], raise_error=False, mtype="all",
                                    ret_type="zeros")
            og_mask[og_mask==parameter_dict["mask_properties"]["membrane"]["index"]]=0 # deleting old membrane mask
            og_mask[mask] = parameter_dict["mask_properties"]["membrane"]["index"]
            db.setMask(frame=db_info["frames_ref_dict"][frame], data=og_mask.astype("uint8"))
    return im_filter



def deformation(frame, parameter_dict,res_dict, db,db_info=None,masks=None,**kwargs):

    # deformation for 1 frame
    im1 = db.getImage(id=db_info["file_order"][frame + "images_after"]).data  ## thats very slow though
    im2 = db.getImage(id=db_info["file_order"][frame + "images_before"]).data

    # overlapp and windowsize in pixels
    window_size_pix=int(np.ceil(parameter_dict["window_size"] / parameter_dict["pixelsize"]))
    overlapp_pix=int(np.ceil(parameter_dict["overlapp"] / parameter_dict["pixelsize"]))
    u, v, x, y, mask, mask_std = calculate_deformation(im1.astype(np.int32), im2.astype(np.int32),
                                                      window_size_pix, overlapp_pix,
                                                       std_factor=parameter_dict["std_factor"])
    db_info["defo_shape"]=u.shape
    res_dict[frame]["sum deformations"].append(["image",np.sum(np.sqrt(u ** 2 + v** 2)),""]) # propably remove that

    # adding plot of derformation field to the database
    add_plot("deformation", (u,v), show_quiver,frame,db_info,default_fig_parameters,parameter_dict,db)

    # saving raw files
    np.save(os.path.join(db_info["path"], frame + "u.npy"), u)
    np.save(os.path.join(db_info["path"], frame + "v.npy"), v)
    # summing deformation over certain areas
    mtypes = [m for m in db_info["mask_types"] if m in get_masks_by_key(default_parameters,"use","defo")]
    sum_on_area(frame,res_dict,parameter_dict,"sum deformations",masks,mask_types=mtypes,x=u,y=v,sumtype="abs")
    return None, frame




def get_contractillity_contractile_energy(frame, parameter_dict,res_dict, db,db_info=None,masks=None,**kwargs):

    u, v = try_to_load_deformation(db_info["path"], frame, warn=True)
    t_x, t_y = try_to_load_traction(db_info["path"], frame, warn=False)
    db_info["defo_shape"]=t_x.shape
    ps_new = parameter_dict["pixelsize"] * np.mean(np.array(db_info["im_shape"][frame]) / np.array(t_x.shape))
    # select masks
    mtypes = [m for m in db_info["mask_types"] if m in get_masks_by_key(default_parameters,"use","forces")]
    if isinstance(u, np.ndarray):
        energy_points = contractile_energy_points(u, v, t_x, t_y, parameter_dict["pixelsize"], ps_new)  # contractile energy at any point
        # plotting contractile energy (only happens if enable in default_fig_parameters
        add_plot("energy_points",[energy_points],show_map_clickpoints,frame,db_info,default_fig_parameters,parameter_dict,db)

    # iterating though mask that are selected for summation
    mask_iter = masks.reconstruct_masks_frame(frame, mtypes, raise_error=True, fill_holes=True)
    contractile_force = None
    contr_energy = None
    for obj_id, mask, mtype, warn in mask_iter:
        if not isinstance(mask, np.ndarray):
            print("couldn't identify mask %s in frame %s patch %s" % (str(mtype), str(frame), str(obj_id)))
            continue
        # interpolation to size of traction force array
        mask_int = interpolation(mask, t_x.shape)
        # calculate contractillity only in "colony" mode
        if parameter_dict["FEM_mode"]=="colony":
            contractile_force, proj_x, proj_y,center=contractillity(t_x, t_y, ps_new, mask_int)
            res_dict[frame]["contractillity on " + default_parameters["mask_properties"][mtype]["label"]].append([obj_id,contractile_force, warn])
        # calculate contractile energy if deformations are provided
        if isinstance(u,np.ndarray):
            check_shape(u, t_x)
            contr_energy = np.sum(energy_points[mask_int])  # sum of contractile energy on on mask
            res_dict[frame]["contractile energy on " + default_parameters["mask_properties"][mtype]["label"]].append([obj_id,contr_energy, warn])
        print("contractile energy=",round_flexible(contr_energy),"contractillity=",round_flexible(contractile_force))

    return (contractile_force, contr_energy), frame




def traction_force(frame, parameter_dict,res_dict, db, db_info=None,masks=None,**kwargs):

    # trying to laod deformation
    u,v=try_to_load_deformation(db_info["path"], frame, warn=False)
    db_info["defo_shape"] = u.shape
    ps_new = parameter_dict["pixelsize"] * np.mean(  # should be equivalent to "pixelsize_def_image"
            np.array(db_info["im_shape"][frame]) / np.array(u.shape))

    # using tfm with or without finite thickness correction
    if parameter_dict["TFM_mode"] == "finite_thickness":
        tx, ty = ffttc_traction_finite_thickness_wrapper(u, v, pixelsize1=parameter_dict["pixelsize"],
                                                     pixelsize2=ps_new,
                                                     h=parameter_dict["h"], young=parameter_dict["young"],
                                                     sigma=parameter_dict["sigma"],
                                                     filter="gaussian")

    if parameter_dict["TFM_mode"] == "infinite_thickness":
        tx, ty = ffttc_traction(u, v, pixelsize1=parameter_dict["pixelsize"],
                                                 pixelsize2=ps_new,
                                                 young=parameter_dict["young"],
                                                 sigma=parameter_dict["sigma"],
                                                 filter="gaussian")


    # add a plot of the trackitoon filed to the database
    add_plot("traction", (tx,ty),show_quiver,frame,db_info,default_fig_parameters,parameter_dict,db)

    # saving raw files
    np.save(os.path.join(db_info["path"], frame + "tx.npy"), tx)
    np.save(os.path.join(db_info["path"], frame + "ty.npy"), ty)

    mtypes = [m for m in db_info["mask_types"] if m in get_masks_by_key(default_parameters,"use","forces")]
    sum_on_area(frame,res_dict,parameter_dict, "sum traction forces", masks,mask_types=mtypes, x=tx, y=ty, sumtype="abs")
    return None, frame


def FEM_grid_setup(frame,parameter_dict,mask_grid,db_info=None,warn="",**kwargs):
    '''

    :param frame:
    :param parameter_dict:
    :param db:
    :param db_info:
    :param kwargs:
    :return:
    '''
    # loading forces and update shape info
    t_x, t_y = try_to_load_traction(db_info["path"], frame, warn=False)
    db_info["defo_shape"] = t_x.shape  # pixelsize of fem grid in µm
    ps_new = parameter_dict["pixelsize"] * np.mean(np.array(db_info["im_shape"][frame]) / np.array(t_x.shape))
    # preparig the mask
    mask_area = prepare_mask_FEM(mask_grid, t_x.shape)  # area for FEM analysis
    warn_grid = warn_small_FEM_area(mask_area, threshold=1000)
    warn = warn + " " + warn_grid

    # FEM grid setup
    # preparing forces
    f_x = t_x * ((ps_new * (10 ** -6)) ** 2)  # point force for each node from tractions
    f_y = t_y * ((ps_new * (10 ** -6)) ** 2)
    if parameter_dict["FEM_mode"]=="colony":
        # using mask for grid setup
        # trying to load cell colony mask, raise error if not found
       # coorecting force for torque and net force
        f_x[~mask_area] = np.nan  # setting all values outside of mask area to zero
        f_y[~mask_area] = np.nan
        f_x_c1 = f_x - np.nanmean(f_x)  # normalizing traction force to sum up to zero (no displacement)
        f_y_c1 = f_y - np.nanmean(f_y)
        f_x_c2, f_y_c2, p = correct_torque(f_x_c1, f_y_c1, mask_area)
        # get_torque1(f_y,f_x,mask_area)
        nodes, elements, loads, mats = grid_setup(mask_area, -f_x_c2, -f_y_c2, 1, sigma = parameter_dict["sigma"],edge_factor=parameter_dict["edge_padding"])  # note the negative signe

    if parameter_dict["FEM_mode"] == "cell layer":
        nodes, elements, loads, mats = grid_setup(mask_area, -f_x, -f_y, 1, sigma = parameter_dict["sigma"],edge_factor=parameter_dict["edge_padding"])  # note the negative signe


    return nodes, elements, loads, mats, mask_area, warn, ps_new



def FEM_simulation(nodes, elements, loads, mats, mask_area, system_type, verbose=False, **kwargs):

    DME, IBC, neq = ass.DME(nodes, elements)  # boundary conditions asembly??
    print("Number of elements: {}".format(elements.shape[0]))
    print("Number of equations: {}".format(neq))

    # System assembly
    KG = ass.assembler(elements, mats, nodes, neq, DME, sparse=True)
    RHSG = ass.loadasem(loads, IBC, neq)

    # System solution with custom conditions
    if system_type=="colony":
        # solver with constraints to zero translation and zero rotation
        UG_sol, rx = custom_solver(KG, RHSG, mask_area, verbose=verbose)

    # System solution with default solver
    if system_type == "cell layer":
        UG_sol = sol.static_sol(KG, RHSG)  # automatically detect sparce matrix
        if not (np.allclose(KG.dot(UG_sol) / KG.max(), RHSG / KG.max())):
            print("The system is not in equilibrium!")

    # average shear and normal stress on the colony area
    UC = pos.complete_disp(IBC, nodes, UG_sol)  # uc are x and y displacements
    E_nodes, S_nodes = pos.strain_nodes(nodes, elements, mats, UC)  # stresses and strains
    stress_tensor = calculate_stress_tensor(S_nodes, nodes, dims=mask_area.shape)  # assembling the stress tensor
    return  UG_sol,stress_tensor





def FEM_analysis_average_stresses(frame,res_dict,parameter_dict, db,db_info,stress_tensor,ps_new, masks, obj_id,**kwargs):

    # analyzing the FEM results with average stresses
    shear=stress_tensor[:,:,0,1] # shear component of the stress tensor
    mean_normal_stress =(stress_tensor[:,:,0,0]+stress_tensor[:,:,1,1])/2 # mean normal component of the stress tensor
    shear=shear/(ps_new*10**-6)# conversion to N/m
    mean_normal_stress=mean_normal_stress/(ps_new*10**-6)# conversion to N/m
    if parameter_dict["FEM_mode"] == "cell layer":
        use_type="stress_layer"
        add_cut_factor = parameter_dict["edge_padding"]+parameter_dict["padding_cell_layer"]   ## include in parameters
    else:
        use_type = "stress_colony"
        add_cut_factor = None
    #all mask types used for summing
    mtypes = [m for m in db_info["mask_types"] if m in get_masks_by_key(default_parameters, "use", use_type)]
    sum_on_area(frame, res_dict, parameter_dict, "mean normal stress", masks, mask_types=mtypes, obj_ids=[obj_id], x=mean_normal_stress,
                sumtype="mean",add_cut_factor=add_cut_factor)
    sum_on_area(frame, res_dict, parameter_dict, "shear stress", masks, mask_types=mtypes, obj_ids=[obj_id], x=shear,
                sumtype="mean",add_cut_factor=add_cut_factor)
    ### other possible stress measures, just for a nice picture
    #sigma_max, sigma_min, tau_max, phi_n, phi_shear, sigma_avg = all_stress_measures(S_nodes, nodes,
     #                                                                                dims=mask_area.shape)
    #sigma_max_abs = np.maximum(np.abs(sigma_min), np.abs(sigma_max))  ### highest possible norm of the stress tensor
    return mean_normal_stress

def FEM_analysis_borders(frame, res_dict, db,db_info,parameter_dict, stress_tensor, ps_new, borders,obj_id, warn,**kwargs):

    # retrieving spline representation of borders
    lines_splines = borders.lines_splines
    line_lengths = borders.line_lengths
    # plot lines tresses over border as continuous curves:
    lines_interpol, min_v, max_v = interpolation_for_stress_and_normal_vector(lines_splines, line_lengths,
                                                                              stress_tensor, pixel_length=ps_new,
                                                                              interpol_factor=1)
    plot_values=(borders.inter_shape,borders.edge_lines, lines_interpol, min_v, max_v)

    avg_line_stress = mean_stress_vector_norm(lines_interpol, borders, norm_level="points", vtype="t_vecs",exclude_colony_edge=True)
    res_dict[frame]["avarage line tension"].append([obj_id, avg_line_stress[1], warn])
    res_dict[frame]["std line tension"].append([obj_id,avg_line_stress[2],""])
    if parameter_dict["FEM_mode"]=="colony": # currently cells are not detected in cell layer mode// could ne implemented though...
        avg_cell_force = mean_stress_vector_norm(lines_interpol, borders, norm_level="cells", vtype="t_vecs",exclude_colony_edge=True)
        avg_cell_pressure = mean_stress_vector_norm(lines_interpol, borders, norm_level="cells", vtype="t_normal",exclude_colony_edge=True)
        avg_cell_shear = mean_stress_vector_norm(lines_interpol, borders, norm_level="cells", vtype="t_shear",exclude_colony_edge=True)
        res_dict[frame]["avarage cell force"].append([obj_id, avg_cell_force[1], warn])
        res_dict[frame]["avarage cell pressure"].append([obj_id, avg_cell_pressure[1], warn])
        res_dict[frame]["avarage cell shear"].append([obj_id, avg_cell_shear[1], warn])
        res_dict[frame]["std cell force"].append([obj_id, avg_cell_force[2], ""])
        res_dict[frame]["std cell pressure"].append([obj_id, avg_cell_pressure[2], ""])
        res_dict[frame]["std cell shear"].append([obj_id, avg_cell_shear[2], ""])




    return None, frame,plot_values

def FEM_full_analysis(frame, parameter_dict,res_dict, db, db_info=None,masks=None, **kwargs):
    # performing full MSM/finite elements analysis
    #wrapper to flexibly perform FEM analysis
    # masks for FEM grid
    plot_values = []
    m_stresses = []
    FEM_type="FEM_layer" if parameter_dict["FEM_mode"]=="cell layer" else "FEM_colony"
    FEM_area_masks=get_masks_by_key(parameter_dict,"use",FEM_type) # masks that make up the FEM_area
    # add relevant masks, usefull in cell layer mode
    mask_iter_grid = masks.reconstruct_masks_frame_add(frame, FEM_area_masks, raise_error=False, fill_holes=True)
    # masks for line tension along cell-cell borders
    mask_iter_borders = masks.reconstruct_masks_frame(frame, "membrane", raise_error=True, fill_holes=False)
    for obj_id, mask_grid, mtype, warn in mask_iter_grid:
        if not isinstance(mask_grid, np.ndarray):
            print("couldn't identify FEM_area in frame %s patch %s" % (str(frame), str(obj_id)))
            continue # skip if FEM_area for this patch is empty
        nodes, elements, loads, mats, mask_area, warn, ps_new = FEM_grid_setup(frame, parameter_dict, mask_grid,
                                                                                          db_info=db_info, **kwargs)
        # FEM solution
        UG_sol, stress_tensor = FEM_simulation(nodes, elements, loads, mats, mask_area, parameter_dict["FEM_mode"], frame=frame)
        np.save(os.path.join(db_info["path"], frame + "stress_tensor.npy"), stress_tensor)

        # analyzing stresses and stress distribution ####### TODO: implement coefficient of variation here
        mean_normal_stress = FEM_analysis_average_stresses(frame, res_dict, parameter_dict, db, db_info, stress_tensor,
                                                           ps_new, masks, obj_id)
        m_stresses.append(mean_normal_stress)

        # finding cell borders
        mask_borders, warn_borders = next(((v[1],v[3]) for v in mask_iter_borders if v[0]==obj_id),(None,""))
        if parameter_dict["FEM_mode"]=="layer": # additional cutting when in layer mode
            mask_borders,w=cut_mask_from_edge(mask_borders,parameter_dict["edge_padding"]+parameter_dict["padding_cell_layer"],parameter_dict["TFM_mode"]=="colony")
        if isinstance(mask_borders,np.ndarray):
            warn += " " + warn_borders
            borders = find_borders(mask_borders, mask_area.shape,raise_error=False,type=parameter_dict["FEM_mode"])
            if not isinstance(borders,Cells_and_Lines): # maybe print something here
                print("couldn't identify cell borders in frame %s patch %s"%(str(frame),str(obj_id)))
                continue
            # analyzing line tension
            k,f,pv=FEM_analysis_borders(frame, res_dict, db,db_info,parameter_dict, stress_tensor, ps_new, borders,obj_id, warn,
                                 **kwargs)
            plot_values.append(pv)
    # plotting the stress at cell borders
    add_plot("FEM_borders", [plot_values], plot_continous_boundary_stresses, frame, db_info, default_fig_parameters, parameter_dict, db)
    # plotting the stress on the colony area
    m_stresses=np.sum(m_stresses,axis=0)
    add_plot("stress_map", [m_stresses], show_map_clickpoints, frame, db_info, default_fig_parameters,
             parameter_dict, db)


def provide_basic_objects(db,frames,parameter_dict,db_info,masks,res_dict):
    if not isinstance(db_info, dict):
        db_info, all_frames = get_db_info_for_analysis(db)
    if not isinstance(masks, cells_masks):
        masks = cells_masks(frames, db, db_info, parameter_dict) # create new masks object if necessary
    else: # add frames that are missing
        for frame in frames:
            if frame not in masks.masks_dict.keys():
                masks.add_frames(frame,parameter_dict["min_obj_size"])

    if isinstance(res_dict, defaultdict):
        if isinstance(res_dict.default_factory(), defaultdict):
            if isinstance(res_dict.default_factory().default_factory(), list):
                return db_info,masks,res_dict
    res_dict = defaultdict(lambda: defaultdict(list))
    return db_info,masks,res_dict

def apply_to_frames(db, parameter_dict, analysis_function,leave_basics=False,res_dict=None,frames=[],db_info=None,masks=None,**kwargs):
    '''
    wrapper to apply analysis function on all frames
    :param db: clickpoints database
    :param parameter_dict: parameters for piv deforamtion calcualtion: (windowsize, overlapp), sigma and youngs modulus
    of the gel (or of the cell sheet when applying FEM), hight of the gel
    :param func: function that is analyzed
    :param frames: list f frames (of the cdb database) to be analyze e.g [0,1,2]
    :param db_info: dicitionary with the keys "path","frames_ref_dict","im_shape","file_order" as constructed from
    get db_info_for_analysis
    :param res_dict: ictionary of results to be filled up adn appended
    :return:
    '''

    frames = make_iterable(frames)  # if only one frame is provided
    if not leave_basics==True:
        db_info, masks, res_dict = provide_basic_objects(db,frames, parameter_dict, db_info, masks, res_dict)
    print(calculation_messages[analysis_function.__name__] % str(frames))
    for frame in tqdm(frames,total=len(frames)):
        try:
            analysis_function(frame, parameter_dict, res_dict, db=db,db_info=db_info,masks=masks,**kwargs)
        except Exception as e:
            if type(e) in (Mask_Error,FileNotFoundError,FindingBorderError,ShapeMismatchError):
                print(e)
            else:
                raise(e)
    return db_info, masks, res_dict


### code to work on clickpoint outside of the addon
if __name__=="__main__":
    ## setting up necessary paramteres
    #db=clickpoints.DataFile("/home/user/Desktop/Monolayers_new_images/monolayers_new_images/KO_DC1_tomatoshift/database.cdb","r")
    db = clickpoints.DataFile(
        "/home/user/Desktop/backup_from_harddrive/data_traction_force_microscopy/Monolayers_new_images/KO_DC1_tomatoshift/database_new.cdb", "r")
    parameter_dict = default_parameters
    res_dict=defaultdict(lambda: defaultdict(list))
    db_info, all_frames = get_db_info_for_analysis(db)
    parameter_dict["overlapp"]=10
    parameter_dict["window_size"] = 20
    parameter_dict["FEM_mode"] = "cell layer"
        #parameter_dict["FEM_mode"] = "colony"
        #default_fig_parameters["cmap"]="jet"
        #default_fig_parameters["vmax"] = {"traction":500,"FEM_borders":0.03}
        #default_fig_parameters["filter_factor"]=1.5
        #default_fig_parameters["scale_ratio"] = 0.15
        #default_fig_parameters["cbar_style"] = "clickpoints"
        #default_fig_parameters["background_color"]="white"
        #default_fig_parameters["plots"]["colony"].append("energy_points")

        #default_fig_parameters["headwidth"] = 3
        #default_fig_parameters["width"] = 0.003
        #default_fig_parameters["cbar_tick_label_size"] = 35
        #default_fig_parameters["cbar_axes_fraction"] = 0.25
    #
    #mask_membrane = masks.reconstruct_mask("01", 0, "membrane", raise_error=True)

    ###### problem: produces empty entries when try to acces non-exisitng str
    masks = cells_masks(all_frames, db, db_info, parameter_dict)
   # db_info, masks, res_dict = apply_to_frames(db, parameter_dict, simple_segmentation, res_dict, frames="1",
   #                                            db_info=db_info, masks=masks,seg_threshold=0,seg_type="cell_area",)
    db_info, masks, res_dict = apply_to_frames(db, parameter_dict, general_properties, res_dict, frames="1", db_info=db_info, masks=masks)
  #  db_info, masks, res_dict = apply_to_frames(db, parameter_dict, traction_force, res_dict, frames="1", db_info=db_info, masks=masks)
    db_info, masks, res_dict = apply_to_frames(db, parameter_dict, FEM_full_analysis, res_dict, frames="1", db_info=db_info,masks=masks)
    db_info, masks, res_dict = apply_to_frames(db, parameter_dict, traction_force, res_dict, frames="1", db_info=db_info,masks=masks)
    db_info,masks,res_dict=apply_to_frames(db, parameter_dict, get_contractillity_contractile_energy, res_dict, frames="1", db_info=db_info,masks=masks)
   # print(res_dict)
        #apply_to_frames(db, parameter_dict, FEM_full_analysis, res_dict, frames="12", db_info=db_info)

        #apply_to_frames(db, parameter_dict, FEM_full_analysis, res_dict, frames=all_frames, db_info=db_info)

    write_output_file(res_dict, "results", "/home/user/Desktop/backup_from_harddrive/data_traction_force_microscopy/WT_vs_KO_images/WTshift/out_test.txt",new_file=True)
        # calculating the deformation field and adding to data base